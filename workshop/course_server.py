"""Loopback teacher console + restricted LAN audience listener."""
import argparse
import json
import threading
import webbrowser
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pocket_sdl import LabError
from pocket_sdl.config import load_config
from workshop.classroom import ClassroomEngine
from workshop.engine import REPO_ROOT
from workshop.launcher import pico_ports, probe, select_board
from workshop.server import make_handler, detect_lan_ip

MARKER = 'pocket-sdl-classroom-v1'


def course_handler(engine, audience_port, audience=False):
    base = make_handler(engine, '')
    class Handler(base):
        def _current_mobile_url(self):
            return f'http://{detect_lan_ip()}:{audience_port}/mobile' if audience_port else ''

        def do_GET(self):
            parsed = urlparse(self.path)
            path = parsed.path
            if audience and path not in ('/mobile', '/mobile.html', '/api/state', '/api/events'):
                self._send_json(403, {'ok': False, 'error': {'message': '此端口仅供观众出题'}})
                return
            try:
                if path == '/launcher-health':
                    self._send_json(200, {'launcher': MARKER, 'mode': engine.mode})
                elif path == '/':
                    self._send_bytes(200, (Path(__file__).parent/'course.html').read_bytes(), 'text/html; charset=utf-8')
                elif path == '/console':
                    self.path='/'
                    super().do_GET()
                elif path == '/api/course/network':
                    url=self._current_mobile_url()
                    self._send_json(200, {'ok': True, 'url': url, 'enabled': bool(audience_port),
                        'usable_ip': bool(url and not url.startswith('http://127.')), 'port': audience_port})
                elif path == '/api/course/archives':
                    self._send_json(200, {'ok': True, 'archives': engine.archive_list()})
                elif path == '/api/course/export':
                    q=parse_qs(parsed.query); kind=q.get('format',['json'])[0]
                    data=engine.export(q.get('id',[''])[0], kind)
                    mime={'json':'application/json', 'jsonl':'application/x-ndjson','csv':'text/csv'}.get(kind, 'text/plain')
                    self._send_bytes(200, data, mime+'; charset=utf-8')
                elif path.startswith('/wiring/'):
                    names={'overview':'面包板接线图_灯与光谱实物确认版.png','led':'RGB灯_公对公接线示意.png','sensor':'光谱模块_四根公对公接线示意.png'}
                    name=names.get(path.split('/')[-1])
                    if not name: raise LabError('NOT_FOUND','接线图不存在')
                    self._send_bytes(200,(REPO_ROOT/'docs'/name).read_bytes(),'image/png')
                else:
                    super().do_GET()
            except (LabError, ValueError) as e:
                self._send_json(400, {'ok': False, 'error': {'message': str(e)}})

        def do_POST(self):
            path=urlparse(self.path).path
            origin=self.headers.get('Origin')
            if origin and origin != 'http://'+self.headers.get('Host',''):
                self._send_json(403, {'ok': False, 'error': {'message':'请从本操作台页面提交'}})
                return
            if audience and path != '/api/queue/submit':
                self._send_json(403, {'ok': False, 'error': {'message':'手机端只能出题；设备控制请用电脑'}})
                return
            with engine.course_lock:
                if not path.startswith('/api/course/'):
                    return super().do_POST()
                try:
                    b=self._read_json()
                    if path == '/api/course/settings': result=engine.settings(**b)
                    elif path == '/api/course/stability': result=engine.stability_start(**b)
                    elif path == '/api/course/confirm': result=engine.confirm_start(**b)
                    elif path == '/api/course/benchmark': result=engine.benchmark_start(**b)
                    elif path == '/api/course/reliability': result=engine.reliability_start(**b)
                    elif path == '/api/course/fault': result=engine.fault_set(**b)
                    elif path == '/api/course/finish': result=engine.finish_round()
                    else: raise LabError('NOT_FOUND','未知课程接口')
                    self._send_json(200, {'ok': True, 'result': result})
                except (LabError, ValueError, TypeError) as e:
                    self._send_json(400, {'ok': False, 'error': {'code':getattr(e,'code','INVALID_INPUT'),'message':str(e)}})
    return Handler


def main(argv=None):
    p=argparse.ArgumentParser(description='Pocket SDL Classroom 独立课程版')
    p.add_argument('--mode',choices=['physical','simulated'],default='physical')
    p.add_argument('--port',type=int,default=8770)
    p.add_argument('--audience-port',type=int,default=0,help='同局域网观众端口，0 禁用')
    p.add_argument('--serial-number')
    p.add_argument('--no-open',action='store_true')
    p.add_argument('--data-dir')
    args=p.parse_args(argv)
    if not 1<=args.port<=65535 or not 0<=args.audience_port<=65535 or args.port==args.audience_port:
        p.error('请选择不同的有效端口')
    teacher=mobile=engine=None
    mobile_started=False
    url=f'http://127.0.0.1:{args.port}/'
    try:
        # Reserve both ports BEFORE touching hardware.
        try:
            teacher=ThreadingHTTPServer(('127.0.0.1',args.port),course_handler(None,0))
            teacher.daemon_threads=True
        except OSError:
            from urllib.request import urlopen
            try:
                with urlopen(url+'launcher-health',timeout=1) as response:
                    health=json.load(response)
            except (OSError,ValueError):
                health={}
            if health.get('launcher')==MARKER and health.get('mode')==args.mode.upper():
                print('课程操作台已在运行：'+url,flush=True)
                if not args.no_open: webbrowser.open(url)
                return 0
            raise LabError('PORT_BUSY', f'端口 {args.port} 已被占用；请关闭旧服务或选择不同端口')
        if args.audience_port:
            mobile=ThreadingHTTPServer(('0.0.0.0',args.audience_port),course_handler(None,0,True))
            mobile.daemon_threads=True
        serial_port=None
        if args.mode=='physical':
            # Do not interrupt the legacy physical launcher which may own the same board.
            from urllib.request import urlopen
            try:
                with urlopen('http://127.0.0.1:8766/launcher-health',timeout=.5) as r:
                    legacy=json.load(r)
            except (OSError,ValueError):
                legacy={}
            if legacy.get('mode')=='PHYSICAL':
                raise LabError('DEVICE_BUSY','旧真机操作台仍在运行。先在旧启动终端按 Ctrl+C 关灯并释放串口，再启动课程版。')
            ports=select_board(pico_ports(),args.serial_number)
            serial_port=probe(ports,load_config(REPO_ROOT/'configs/physical.json'))
        engine=ClassroomEngine(mode=args.mode.upper(),serial_port=serial_port,data_dir=args.data_dir)
        teacher.RequestHandlerClass=course_handler(engine,args.audience_port)
        if mobile:
            mobile.RequestHandlerClass=course_handler(engine,args.audience_port,True)
            threading.Thread(target=mobile.serve_forever,daemon=True).start()
            mobile_started=True
        print(f'Pocket SDL Classroom [{engine.mode}]\n教师操作台：{url}\n记录目录：{engine.data_dir}\n保持窗口打开；Ctrl+C 退出。',flush=True)
        if mobile: print(f'同 Wi-Fi 手机入口：http://{detect_lan_ip()}:{args.audience_port}/mobile',flush=True)
        if not args.no_open: webbrowser.open(url)
        teacher.serve_forever()
    except KeyboardInterrupt:
        pass
    except (OSError,LabError,EOFError) as e:
        print(f'启动失败：{e}\n请检查端口/设备。不会自动切换模式。',flush=True)
        return 1
    finally:
        if engine:
            engine.queue_auto=False
            engine.shutdown()
            if engine._worker_thread: engine._worker_thread.join(timeout=10)
            engine.checkpoint('shutdown')
        if mobile:
            if mobile_started: mobile.shutdown()
            mobile.server_close()
        if teacher: teacher.server_close()
    return 0

if __name__=='__main__':
    raise SystemExit(main())
