"""Classroom experiments, durable evidence and separate confirmation measurements."""
import copy
import csv
import io
import json
import threading
import time
import uuid
from pathlib import Path

import numpy as np

from pocket_sdl import LabError
from pocket_sdl.core import GRID, relative_stability, spectral_loss
from pocket_sdl.optimize import CandidatePlanner
from pocket_sdl.simulated import SimDevice
from workshop.engine import WorkshopEngine, REPO_ROOT, _utc_iso, snap_x


# Illustrative transmission only; replace with measured films for physical comparison.
COLOR_FILTERS = {
    'red': [.08, .08, .10, .14, .25, .60, .88, .92],
    'green': [.10, .20, .65, .90, .85, .40, .12, .08],
    'blue': [.90, .90, .70, .30, .12, .08, .06, .05],
}


class ClassroomEngine(WorkshopEngine):
    def __init__(self, *args, data_dir=None, **kwargs):
        self.course_lock = threading.RLock()
        self._save_lock = threading.RLock()
        self.data_dir = Path(data_dir or REPO_ROOT / 'classroom-data')
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = time.strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
        self.course = {'session_id': self.session_id, 'group': '', 'prediction': '', 'conclusion': '',
                       'seed': 7, 'budget': 12, 'stability': None, 'confirmations': [],
                       'benchmark': None, 'fault': 'none', 'created_at': _utc_iso()}
        self._ready = False
        self._suppress_target_publish = False
        super().__init__(*args, **kwargs)
        self._ready = True
        self.checkpoint('session_started')

    def snapshot(self):
        with self._state_lock:
            return {'schema_version': 1, 'saved_at': _utc_iso(), 'course': copy.deepcopy(self.course),
                    'config': copy.deepcopy(self.config), 'state': super().get_state(),
                    'arena_private': copy.deepcopy(self.arena)}

    def checkpoint(self, event, data=None):
        if not self._ready:
            return
        with self._save_lock:
            doc = self.snapshot()
            path = self.data_dir / (self.session_id + '.json')
            temp = path.with_suffix('.tmp')
            temp.write_text(json.dumps(doc, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            temp.replace(path)
            # Append-only snapshots preserve overwritten rounds/runs and interrupted jobs.
            with (self.data_dir / (self.session_id + '.jsonl')).open('a', encoding='utf-8') as f:
                f.write(json.dumps({'event': event, 'data': data, 'snapshot': doc},
                                   ensure_ascii=False, allow_nan=False) + '\n')

    def publish(self, event, data):
        if self._suppress_target_publish and event in ('arena_target', 'state'):
            return
        if self._ready and event not in ('sample', 'calibrate_progress'):
            self.checkpoint(event, data)
        super().publish(event, data)

    def get_state(self):
        with self._state_lock:
            state = super().get_state()
            state['course'] = copy.deepcopy(self.course)
            state['capabilities'] = {'physical_benchmark': True}
            return state

    def connect(self):
        if self._ready:
            self._assert_no_active_job()
            self.checkpoint('before_reconnect')
            with self._state_lock:
                self.calibration = None
                self.arena = self._empty_arena()
                self.course['stability'] = None
                self.course['confirmations'] = []
                self.course['benchmark'] = None
        return super().connect()

    def calibrate(self):
        self._assert_no_active_job()
        self._cancel_job.clear()
        self.checkpoint('before_calibration')
        with self._state_lock:
            self.calibration = None
            self.arena = self._empty_arena()
            self.course['stability'] = None
            self.course['confirmations'] = []
            self.course['benchmark'] = None
        try:
            return super().calibrate()
        finally:
            with self._device_lock:
                if self.device: self.device.off()

    def _measure_raw(self, x, turn_off_after=False, color_filter=None, reliability_stage=None, benchmark_context=None):
        if (self._cancel_job.is_set() or self._stop_all.is_set()) and self.active_job is not None:
            raise LabError('CANCELLED', '已停止本次实验')
        used, raw, ms = super()._measure_raw(x, turn_off_after)
        if self.mode == 'SIMULATED' and self.course['fault'] == 'drift':
            raw = np.rint(raw * (0.65 if self.device.sample_count % 2 else 1.35))
        if self.mode == 'SIMULATED' and color_filter is not None:
            dark = np.asarray(self.calibration['dark_mean'])
            raw = np.rint(dark + np.maximum(raw - dark, 0) * COLOR_FILTERS[color_filter])
        self._record_raw(used, raw, ms, color_filter=color_filter or 'none',
                         reliability_stage=reliability_stage, **(benchmark_context or {}))
        return used, raw, ms

    def _record_raw(self, x, raw, ms, **metadata):
        if not self._ready:
            return
        with self._state_lock:
            record = {'ts': _utc_iso(), 'mode': self.mode, 'job': self.active_job,
                      'round_id': self.arena['round_id'], 'x': x,
                      'raw': raw.tolist(), 'duration_ms': ms, **metadata}
        with self._save_lock:
            with (self.data_dir / (self.session_id + '-raw.jsonl')).open('a', encoding='utf-8') as f:
                f.write(json.dumps(record) + '\n')

    def _record_error(self, error):
        if getattr(error, 'code', '') in ('SATURATED', 'SERIAL_TIMEOUT', 'DISCONNECTED', 'DEVICE_ERROR'):
            with self._state_lock:
                q = self.course.get('stability')
                if q:
                    q['passed'] = False
                    q['invalidated_by'] = getattr(error, 'code', '')
        return super()._record_error(error)

    def _require_quality(self):
        with self._state_lock:
            q = self.course['stability']
            if not self.calibration or not q or not q['passed'] or q['calibration_id'] != self.calibration['id']:
                raise LabError('QUALITY_GATE', '先校准，再完成固定点稳定性检查（相对波动 < 5%）')

    def settings(self, group='', prediction='', conclusion='', seed=7, budget=12):
        self._assert_no_active_job()
        seed, budget = int(seed), int(budget)
        if not 0 <= seed <= 2**32-1 or not 6 <= budget <= 24:
            raise LabError('INVALID_INPUT', 'seed 范围 0–4294967295，Random/BO 算法预算 6–24')
        with self._state_lock:
            if self.arena['round_id'] and (seed != self.course['seed'] or budget != self.course['budget']):
                raise LabError('ROUND_LOCKED', '当前轮次参数已锁定；请先结束并归档本轮，再更改种子/预算')
            self.course.update(group=str(group)[:80], prediction=str(prediction)[:2000],
                               conclusion=str(conclusion)[:4000], seed=seed, budget=budget)
            course_copy = copy.deepcopy(self.course)
        self.publish('state', self.get_state())
        return course_copy

    def finish_round(self):
        self._assert_no_active_job()
        self.checkpoint('round_finished')
        with self._state_lock:
            self.arena = self._empty_arena()
            self.course['confirmations'] = []
            self.course['benchmark'] = None
        self.publish('state', self.get_state())
        return {'archived': True}

    def arena_new_target(self, *args, **kwargs):
        self._require_quality()
        self.checkpoint('before_new_target')
        self._suppress_target_publish = True
        try:
            super().arena_new_target(*args, **kwargs)
        finally:
            self._suppress_target_publish = False
        with self._state_lock:
            self.arena['seed'] = self.course['seed']
            self.course['confirmations'] = []
            self.course['benchmark'] = None
            cfg = copy.deepcopy(self.config)
            cfg['seed'] = self.course['seed']
            cfg['measurement']['budget'] = self.course['budget']
            planner = CandidatePlanner(cfg, 'random')
            self.arena['common_initial_x'] = [GRID[i].tolist() for i in planner.random_order[:planner.initial_count]]
        snapshot = self.get_state()
        self.publish('arena_target', snapshot['arena'])
        self.publish('state', snapshot)
        return snapshot['arena']

    def arena_human_start(self, player_name='现场挑战者', budget=12):
        self._require_quality()
        self.checkpoint('before_new_player')
        return super().arena_human_start(player_name, 12 if budget is None else budget)

    def arena_human_measure(self, x):
        self._require_quality()
        if self.arena['human'] is None:
            self.arena_human_start()
        return super().arena_human_measure(x)

    def arena_start_ai(self, strategy='bo', budget=None):
        self._require_quality()
        self.checkpoint('before_search')
        return super().arena_start_ai(strategy, self.course['budget'])

    def queue_run_next(self, budget=16):
        self._require_quality()
        return super().queue_run_next(budget)

    def led_off(self):
        self.queue_auto = False
        self._cancel_job.set()
        return super().led_off()

    def _start_course_job(self, name, work):
        with self._state_lock:
            self._assert_no_active_job()
            self._live_streaming.clear()
            self._cancel_job.clear()
            self.active_job = name
            self.last_error = None
        self.publish('state', self.get_state())
        def run():
            try:
                work()
            except Exception as error:
                self._record_error(error)
            finally:
                with self._device_lock:
                    if self.device: self.device.off()
                with self._state_lock:
                    self.active_job = None
                self.publish('state', self.get_state())
        self._worker_thread = threading.Thread(target=run, name=name, daemon=True)
        self._worker_thread.start()
        return {'started': name}

    def stability_start(self, x=None, repeats=5):
        with self._state_lock:
            if not self.calibration:
                raise LabError('QUALITY_GATE', '请先校准')
            self._assert_no_active_job()
            point = snap_x(x or [0.5, 0.5, 0.5])
            repeats = int(repeats)
            if not 5 <= repeats <= 30:
                raise LabError('INVALID_INPUT', '稳定性重复次数 5–30')
            cal = copy.deepcopy(self.calibration)
            report = {'calibration_id': cal['id'], 'x': point, 'rows': [],
                      'passed': False, 'status': 'running', 'limit': 0.05}
            self.course['stability'] = report
        def work():
            try:
                for _ in range(repeats):
                    _, raw, ms = self._measure_raw(point)
                    with self._state_lock:
                        report['rows'].append({'raw': raw.tolist(), 'duration_ms': ms})
                    self.publish('state', self.get_state())
                with self._state_lock:
                    rows_raw = [r['raw'] for r in report['rows']]
                stats = relative_stability(rows_raw, cal['dark_mean'])
                with self._state_lock:
                    report.update(rms_relative=float(stats['rms_relative']), passed=bool(stats['rms_relative'] < .05), status='completed')
            finally:
                with self._state_lock:
                    if report['status'] == 'running': report['status'] = 'interrupted'
        return self._start_course_job('stability', work)

    def reliability_start(self, stage='baseline', color_filter='red'):
        if self.mode not in ('SIMULATED', 'PHYSICAL'):
            raise LabError('INVALID_STATE', '未知设备模式')
        if not self.device_connected:
            raise LabError('DISCONNECTED', '设备未连接，请检查连接后重新校准')
        self._assert_no_active_job()
        if color_filter not in COLOR_FILTERS:
            raise LabError('INVALID_INPUT', '请选择红、绿或蓝色片')
        if stage not in ('baseline', 'drift', 'recovery'):
            raise LabError('INVALID_INPUT', '未知对照步骤')
        with self._state_lock:
            if not self.calibration:
                raise LabError('QUALITY_GATE', '请先在校准与质检页采集暗场和参考')
            cal = copy.deepcopy(self.calibration)
            experiment = self.course.get('reliability')
            if stage != 'baseline':
                prior = 'baseline' if stage == 'drift' else 'drift'
                if not experiment or experiment['calibration_id'] != cal['id'] or experiment.get('mode', 'SIMULATED') != self.mode:
                    raise LabError('QUALITY_GATE', '校准已变化，请从正常测量重新开始')
                if experiment['stages'].get(prior, {}).get('status') != 'completed':
                    raise LabError('INVALID_STATE', '请先完成上一步对照')
        self.checkpoint('before_reliability_experiment' if stage == 'baseline' else 'before_reliability_stage')
        with self._state_lock:
            if stage == 'baseline':
                experiment = {'calibration_id': cal['id'], 'x': [.5, .5, .5],
                              'created_at': _utc_iso(), 'stages': {}, 'color_filter': color_filter,
                              'mode': self.mode,
                              'model': 'illustrative-transmission-v1' if self.mode == 'SIMULATED' else None}
                self.course['reliability'] = experiment
            elif stage == 'drift':
                experiment['color_filter'] = color_filter
                experiment['stages'].pop('recovery', None)
            report = {'calibration_id': cal['id'], 'x': experiment['x'],
                      'rows': [], 'status': 'running', 'passed': False, 'limit': .05}
            experiment['stages'][stage] = report
            report['mode'] = self.mode
            report['color_filter'] = experiment['color_filter'] if stage == 'drift' else 'none'
            self.course['fault'] = 'none'
            measurement_filter = experiment['color_filter'] if stage == 'drift' else None
            point = list(experiment['x'])
        if self.mode == 'SIMULATED':
            with self._device_lock:
                self.device.fail_at = self.device.saturated_at = None
        def work():
            try:
                for _ in range(5):
                    _, raw, ms = self._measure_raw(point, color_filter=measurement_filter, reliability_stage=stage)
                    with self._state_lock:
                        report['rows'].append({'raw': raw.tolist(), 'duration_ms': ms})
                    self.publish('state', self.get_state())
                    if self.sim_delay_s: time.sleep(self.sim_delay_s)
                with self._state_lock:
                    rows_raw = [r['raw'] for r in report['rows']]
                stats = relative_stability(rows_raw, cal['dark_mean'])
                with self._state_lock:
                    report.update(mean_raw=np.mean(rows_raw, axis=0).tolist(),
                                  rms_relative=float(stats['rms_relative']),
                                  passed=bool(stats['rms_relative'] < .05), status='completed')
            finally:
                # Film is confined to this experiment; it cannot affect subsequent searches.
                with self._state_lock:
                    if report['status'] == 'running': report['status'] = 'interrupted'
        return self._start_course_job('measurement_reliability', work)

    def confirm_start(self, repeats=3):
        self._require_quality()
        self._assert_no_active_job()
        repeats = int(repeats)
        if not 3 <= repeats <= 30:
            raise LabError('INVALID_INPUT', '独立复测次数 3–30')
        with self._state_lock:
            runs = {'human': self.arena.get('human'), **self.arena['ai_runs']}
            points = [(name, copy.deepcopy(run)) for name, run in runs.items() if run and run['best_x'] is not None]
            if not points:
                raise LabError('INVALID_STATE', '至少先完成人类或算法的一次搜索')
            cal = copy.deepcopy(self.calibration)
            target = copy.deepcopy(self.arena['target_raw'])
            report = {'id': uuid.uuid4().hex[:8], 'round_id': self.arena['round_id'], 'repeats': repeats,
                      'status': 'running', 'results': {}, 'created_at': _utc_iso()}
            self.course['confirmations'].append(report)
        def work():
            try:
                for name, run in points:
                    row = {'x': run['best_x'], 'search_best': run['best_loss'], 'search_steps': len(run['rows']), 'rows': []}
                    with self._state_lock:
                        report['results'][name] = row
                    for _ in range(repeats):
                        x, raw, ms = self._measure_raw(row['x'])
                        loss = spectral_loss(raw, cal['dark_mean'], cal['reference_scale'], target)
                        with self._state_lock:
                            row['rows'].append({'x': x, 'raw': raw.tolist(), 'loss': float(loss), 'duration_ms': ms})
                            values = [r['loss'] for r in row['rows']]
                            row.update(mean=float(np.mean(values)), sample_sd=float(np.std(values, ddof=1)) if len(values)>1 else None)
                        self.publish('state', self.get_state())
                with self._state_lock:
                    report['status'] = 'completed'
            finally:
                with self._state_lock:
                    if report['status'] == 'running': report['status'] = 'interrupted'
        return self._start_course_job('confirmation', work)

    def fault_set(self, kind='none'):
        if self.mode != 'SIMULATED':
            raise LabError('SIMULATION_ONLY', '故障注入仅供仿真演练')
        self._assert_no_active_job()
        if kind not in ('none', 'timeout', 'saturation', 'drift'):
            raise LabError('INVALID_INPUT', '未知故障类型')
        with self._device_lock:
            self.device.fail_at = self.device.sample_count + 1 if kind == 'timeout' else None
            self.device.saturated_at = self.device.sample_count + 1 if kind == 'saturation' else None
        with self._state_lock:
            self.course['fault'] = kind
            self.course['stability'] = None
        self.publish('state', self.get_state())
        return {'fault': kind}

    def benchmark_start(self, seeds=None, budgets=None):
        if self.mode not in ('SIMULATED', 'PHYSICAL'):
            raise LabError('INVALID_STATE', '未知设备模式')
        if not self.device_connected:
            raise LabError('DISCONNECTED', '设备未连接，请重新连接并校准')
        self._require_quality()
        self._assert_no_active_job()
        seeds = [int(s) for s in ([1, 2, 3, 4, 5] if seeds is None else seeds)]
        budgets = sorted(set(int(b) for b in ([8, 16, 24] if budgets is None else budgets)))
        if not 2 <= len(seeds) <= 10 or len(set(seeds)) != len(seeds) or any(s < 0 or s >= 2**32 for s in seeds):
            raise LabError('INVALID_INPUT', '提供 2–10 个不同的非负种子')
        if not budgets or len(budgets)>5 or any(b < 6 or b > 36 for b in budgets):
            raise LabError('INVALID_INPUT', '预算 6–36，最多 5 档')
        self.checkpoint('before_benchmark')
        with self._state_lock:
            if self.course['fault'] != 'none' or not self.arena['round_id']:
                raise LabError('INVALID_STATE', '请解除故障并建立固定目标')
            cal, target = copy.deepcopy(self.calibration), copy.deepcopy(self.arena['target_raw'])
            report = {'id': uuid.uuid4().hex[:8], 'mode': self.mode, 'status': 'running',
                      'calibration_id': cal['id'], 'completed_measurements': 0,
                      'total_measurements': len(seeds) * 2 * max(budgets), 'seeds': seeds, 'budgets': budgets,
                      'round_id': self.arena['round_id'], 'target_raw': target, 'runs': [], 'summary': [],
                      'tie_tolerance': 0.001, 'policy': '各预算取同一最大预算轨迹前缀；两算法使用相同初始候选点。' +
                      ('仿真测量噪声使用相同独立种子。' if self.mode == 'SIMULATED' else '真机逐次采集，不复制读数；不同种子交替两算法的执行顺序。')}
            self.course['benchmark'] = report
        def work():
            try:
                for seed_index, seed in enumerate(seeds):
                    strategies = ['bo', 'random'] if self.mode == 'PHYSICAL' and seed_index % 2 else ['random', 'bo']
                    for strategy in strategies:
                        cfg = copy.deepcopy(self.config)
                        cfg['seed'] = seed
                        cfg['measurement']['budget'] = max(budgets)
                        if self.mode == 'SIMULATED':
                            cfg['simulation']['seed'] = seed + 100000
                        planner = CandidatePlanner(cfg, strategy)
                        dev = SimDevice(cfg) if self.mode == 'SIMULATED' else None
                        run = {'seed': seed, 'strategy': strategy, 'rows': []}
                        with self._state_lock:
                            report['runs'].append(run)
                        xs, ys = [], []
                        try:
                            for i in range(max(budgets)):
                                if self._cancel_job.is_set() or self._stop_all.is_set():
                                    raise LabError('CANCELLED', '批量研究已停止')
                                x = GRID[planner.choose(i, xs, ys)].tolist()
                                context = {'benchmark_id': report['id'], 'seed': seed,
                                           'strategy': strategy, 'iteration': i+1}
                                if dev is None:
                                    _, raw, ms = self._measure_raw(x, turn_off_after=True, benchmark_context=context)
                                else:
                                    raw = dev.measure(x)
                                    self._record_raw(x, raw, 0, **context)
                                loss = float(spectral_loss(raw, cal['dark_mean'], cal['reference_scale'], target))
                                xs.append(x); ys.append(loss)
                                with self._state_lock:
                                    run['rows'].append({'iteration': i+1, 'x': x, 'raw': raw.tolist(), 'loss': loss, 'best_so_far': min(ys)})
                                    report['completed_measurements'] += 1
                                if self.mode == 'PHYSICAL':
                                    self.publish('state', self.get_state())
                            self.publish('state', self.get_state())
                        finally:
                            if dev is not None: dev.close()
                if self._cancel_job.is_set() or self._stop_all.is_set():
                    raise LabError('CANCELLED', '批量研究已停止')
                with self._state_lock:
                    for budget in budgets:
                        values = {s: [r['rows'][budget-1]['best_so_far'] for r in report['runs'] if r['strategy']==s] for s in ['random','bo']}
                        delta = np.array(values['random']) - np.array(values['bo'])
                        report['summary'].append({'budget': budget, 'bo_wins': int(sum(delta > .001)), 'ties': int(sum(abs(delta) <= .001)),
                             'bo_losses': int(sum(delta < -.001)), **{s: {'mean': float(np.mean(v)), 'worst': float(max(v))} for s,v in values.items()}})
                    report['status'] = 'completed'
            finally:
                with self._state_lock:
                    if report['status'] == 'running': report['status'] = 'interrupted'
        return self._start_course_job('physical_benchmark' if self.mode == 'PHYSICAL' else 'simulation_benchmark', work)

    def archive_list(self):
        result=[]
        for path in sorted(self.data_dir.glob('*.json'), reverse=True):
            doc=json.loads(path.read_text(encoding='utf-8'))
            result.append({'id': path.stem, 'group': doc['course']['group'], 'mode': doc['state']['mode'], 'saved_at': doc['saved_at']})
        return result

    def export(self, session_id, kind='json'):
        if not session_id or any(c not in '0123456789-abcdef' for c in session_id):
            raise LabError('INVALID_INPUT', '无效归档编号')
        path = self.data_dir / (session_id + '.json')
        if not path.is_file(): raise LabError('NOT_FOUND', '未找到归档')
        with self._save_lock:
            if kind == 'json': return path.read_bytes()
            if kind == 'jsonl': return path.with_suffix('.jsonl').read_bytes()
            if kind == 'csv':
                out=io.StringIO(); writer=csv.writer(out)
                writer.writerow(['timestamp','mode','job','round_id','R','G','B',*[f'F{i}' for i in range(1,9)],'duration_ms','color_filter','reliability_stage','benchmark_id','seed','strategy','iteration'])
                raw_path=self.data_dir/(session_id+'-raw.jsonl')
                if raw_path.exists():
                    for line in raw_path.read_text(encoding='utf-8').splitlines():
                        r=json.loads(line); writer.writerow([r['ts'],r['mode'],r['job'],r['round_id'],*r['x'],*r['raw'],r['duration_ms'],r.get('color_filter','none'),r.get('reliability_stage') or '',r.get('benchmark_id',''),r.get('seed',''),r.get('strategy',''),r.get('iteration','')])
                return ('\ufeff'+out.getvalue()).encode('utf-8')
        raise LabError('INVALID_INPUT', '格式应为 json/jsonl/csv')
