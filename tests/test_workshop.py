"""Workshop Web 实时交互台、离线二维码与 Notebook 全链路测试。"""

import json
import threading
import time
import unittest
import urllib.request
from pathlib import Path

from workshop.engine import WorkshopEngine, loss_to_score, snap_x
from workshop.notebook_viz import show_color_comparison, show_convergence, spectrum_svg
from workshop.qr import make_matrix, render_svg
from workshop.server import create_server

REPO_ROOT = Path(__file__).resolve().parents[1]


class TestWorkshopQR(unittest.TestCase):
    def test_matrix_and_svg(self):
        matrix = make_matrix("http://192.168.1.23:8765/mobile")
        size = len(matrix)
        self.assertIn(size, (21, 25, 29, 33, 37, 41))
        self.assertTrue(all(len(row) == size for row in matrix))
        # 左上角定位框 7x7 外框为全 1
        for i in range(7):
            self.assertEqual(matrix[0][i], 1)
            self.assertEqual(matrix[6][i], 1)
            self.assertEqual(matrix[i][0], 1)
            self.assertEqual(matrix[i][6], 1)
        svg = render_svg("http://192.168.1.23:8765/mobile")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("</svg>", svg)

    def test_payload_limit(self):
        with self.assertRaises(ValueError):
            make_matrix("x" * 120)


class TestWorkshopEngine(unittest.TestCase):
    def setUp(self):
        self.engine = WorkshopEngine(mode="SIMULATED", sim_delay_s=0.0)

    def tearDown(self):
        self.engine.shutdown()

    def test_helpers(self):
        self.assertEqual(snap_x([0.34, 0.56, 1.4]), [0.3, 0.6, 1.0])
        self.assertEqual(loss_to_score(0.0), 100.0)
        self.assertLess(loss_to_score(0.5), loss_to_score(0.1))

    def test_live_and_calibrate_and_arena(self):
        cal = self.engine.calibrate()
        self.assertGreater(cal["reference_scale"], 10.0 * cal["dark_noise"])

        sample = self.engine.live_measure_once([0.2, 0.8, 0.4])
        self.assertEqual(len(sample["raw"]), 8)
        self.assertEqual(len(sample["normalized"]), 8)
        self.assertEqual(self.engine.device.rgb, [0, 0, 0])

        arena = self.engine.arena_new_target(kind="mystery", hide_target=True)
        self.assertIsNotNone(arena["round_id"])
        self.assertIsNone(arena["target_x"])
        self.assertFalse(arena["revealed"])

        self.engine.arena_human_start("测试选手", budget=5)
        res = self.engine.arena_human_measure([0.5, 0.5, 0.5])
        self.assertEqual(res["row"]["iteration"], 1)
        self.assertEqual(len(res["arena"]["leaderboard"]), 1)

        # 运行 Random 与 BO 各 8 步，校验前 4 个候选索引一致
        self.engine.arena_start_ai("random", budget=8)
        self._wait_idle()
        self.engine.arena_start_ai("bo", budget=8)
        self._wait_idle()

        state = self.engine.get_state()
        rand_rows = state["arena"]["ai_runs"]["random"]["rows"]
        bo_rows = state["arena"]["ai_runs"]["bo"]["rows"]
        self.assertEqual(len(rand_rows), 8)
        self.assertEqual(len(bo_rows), 8)
        self.assertEqual(
            [r["candidate_index"] for r in rand_rows[:4]],
            [r["candidate_index"] for r in bo_rows[:4]],
        )
        self.assertTrue(len(state["arena"]["ai_runs"]["bo"]["brain"]["top3"]) == 3)

        revealed = self.engine.arena_reveal()
        self.assertTrue(revealed["revealed"])
        self.assertEqual(len(revealed["target_x"]), 3)

    def test_queue_end_to_end(self):
        task = self.engine.queue_submit("观众A", "抹茶绿", [0.3, 0.7, 0.4])
        self.assertEqual(task["status"], "queued")
        self.engine.queue_run_next(budget=8)
        self._wait_idle()
        q = self.engine.get_state()["queue"]
        self.assertEqual(len(q["pending"]), 0)
        self.assertEqual(len(q["gallery"]), 1)
        done = q["gallery"][0]
        self.assertEqual(done["status"], "completed")
        self.assertEqual(done["steps"], 8)
        self.assertIsNotNone(done["best_x"])

    def test_live_stream_emits_events(self):
        sub = self.engine.subscribe()
        try:
            self.engine.set_live_stream(True, [0.4, 0.2, 0.6])
            got_sample = False
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                item = sub.get(timeout=1.0)
                if item["event"] == "sample":
                    got_sample = True
                    break
            self.assertTrue(got_sample)
        finally:
            self.engine.set_live_stream(False)
            self.engine.unsubscribe(sub)
        self.assertEqual(self.engine.device.rgb, [0, 0, 0])

    def _wait_idle(self, timeout=5.0):
        deadline = time.monotonic() + timeout
        while self.engine.active_job is not None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIsNone(self.engine.active_job)


class TestWorkshopHTTPAndNotebook(unittest.TestCase):
    def test_http_endpoints(self):
        engine = WorkshopEngine(mode="SIMULATED", sim_delay_s=0.0)
        server, _ = create_server(engine, host="127.0.0.1", port=0)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{port}"
            with urllib.request.urlopen(f"{base}/") as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn("Pocket SDL 光谱自驱动实验室", resp.read().decode("utf-8"))
            with urllib.request.urlopen(f"{base}/mobile") as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn("给 Pico 实验室出个颜色", resp.read().decode("utf-8"))
            with urllib.request.urlopen(f"{base}/api/qr.svg") as resp:
                self.assertEqual(resp.status, 200)
                self.assertIn("<svg", resp.read().decode("utf-8"))
            with urllib.request.urlopen(f"{base}/api/state") as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["state"]["mode"], "SIMULATED")

            req = urllib.request.Request(
                f"{base}/api/live/measure",
                data=json.dumps({"x": [0.1, 0.5, 0.9]}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(payload["ok"])
                self.assertEqual(payload["sample"]["x"], [0.1, 0.5, 0.9])
        finally:
            server.shutdown()
            server.server_close()
            engine.shutdown()

    def test_notebook_executes_cleanly(self):
        nb_path = REPO_ROOT / "workshop" / "SDL_Workshop.ipynb"
        nb = json.loads(nb_path.read_text(encoding="utf-8"))
        self.assertEqual(nb["nbformat"], 4)
        self.assertGreaterEqual(len(nb["cells"]), 12)

        ns = {}
        for cell in nb["cells"]:
            if cell["cell_type"] == "code":
                source = "".join(cell["source"])
                exec(compile(source, f"<{cell['id']}>", "exec"), ns, ns)
        self.assertTrue(ns["device"].closed)
        self.assertEqual(len(ns["bo_res"]["best_curve"]), 24)
        self.assertEqual(len(ns["random_res"]["best_curve"]), 24)

    def test_notebook_viz_html(self):
        html = spectrum_svg([100, 200, 300, 400, 500, 400, 300, 200], overlay=[120]*8, x=(0.3, 0.6, 0.4))
        self.assertIn("<svg", html)
        conv = show_convergence({"BO": {"color": "#38bdf8", "best_so_far": [0.4, 0.2, 0.1]}})
        if conv is not None:
            self.assertIn("<svg", conv)
        comp = show_color_comparison([("Target", (0.3, 0.6, 0.4), "x=[0.3,0.6,0.4]")])
        if comp is not None:
            self.assertIn("Target", comp)


if __name__ == "__main__":
    unittest.main()
