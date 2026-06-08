#!/usr/bin/env python3
# === hermes-hive path bootstrap ===
import os
_HERMES_HOME = os.environ.get("HERMES_HOME") or os.path.expanduser("~/.hermes")
_HIVE_DIR = os.path.join(_HERMES_HOME, "hive")
if _HIVE_DIR not in sys.path:
    sys.path.insert(0, _HIVE_DIR)
if _HERMES_HOME not in sys.path:
    sys.path.insert(0, _HERMES_HOME)
# === end bootstrap ===


from __future__ import annotations

import json
import shutil
import sqlite3
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

HIVE_DIR = Path(os.environ.get("HERMES_HOME", "~/.hermes")) / ".hermes/hive"
sys.path.insert(0, str(HIVE_DIR))
import hive_kb as hk  # noqa: E402


class MockLLM(BaseHTTPRequestHandler):
    def do_POST(self):
        _ = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        body = {
            'choices': [
                {
                    'message': {
                        'content': json.dumps(
                            {
                                'problem': '需要修复 SQLite FTS 结构化召回',
                                'outcome_reason': '成功原因是迁移后 fts_text 包含五段字段',
                                'action': '用 ALTER TABLE 补列并重建 FTS 索引',
                                'anti_pattern': '不要只保存大段原始输出',
                                'validation': 'query 返回 problem/action/root_cause',
                                'scope': '适用于 hive_kb 1.4 MVP',
                                'root_cause': 'unknown',
                                'stage': 'finalize',
                                'confidence': 0.82,
                            },
                            ensure_ascii=False,
                        )
                    }
                }
            ]
        }
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        return


def make_old_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute('''
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_type TEXT NOT NULL,
            task_text TEXT NOT NULL,
            huluwa_id INTEGER NOT NULL,
            embedding BLOB,
            experience TEXT NOT NULL,
            tools_used TEXT,
            duration_ms INTEGER,
            success INTEGER NOT NULL,
            fail_reason TEXT,
            use_count INTEGER DEFAULT 0,
            last_used_at REAL,
            decay_score REAL DEFAULT 1.0,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        )
    ''')
    conn.execute('CREATE VIRTUAL TABLE memories_fts USING fts5(task_text, experience, fail_reason)')
    conn.execute('''
        INSERT INTO memories(task_type, task_text, huluwa_id, embedding, experience, tools_used, duration_ms, success, fail_reason, created_at, expires_at)
        VALUES ('code', '老库 FTS 任务', 2, NULL, '旧经验文本', '[]', 10, 1, NULL, 1, 9999999999)
    ''')
    conn.execute('INSERT INTO memories_fts(rowid, task_text, experience, fail_reason) VALUES (1, "老库 FTS 任务", "旧经验文本", "")')
    conn.commit()
    conn.close()


def main() -> None:
    original_db = hk.DB_PATH
    tmpdir = Path(tempfile.mkdtemp(prefix='hive14_smoke_'))
    server = None
    try:
        test_db = tmpdir / 'old_hive_kb.db'
        make_old_db(test_db)
        hk.DB_PATH = test_db
        hk.init_db()

        conn = sqlite3.connect(test_db)
        cols = [row[1] for row in conn.execute('PRAGMA table_info(memories)').fetchall()]
        fts_cols = [row[1] for row in conn.execute('PRAGMA table_info(memories_fts)').fetchall()]
        assert all(c in cols for c in ['problem', 'outcome_reason', 'action', 'anti_pattern', 'validation', 'scope', 'root_cause', 'stage', 'confidence'])
        assert 'fts_text' in fts_cols
        assert conn.execute('SELECT COUNT(*) FROM memories').fetchone()[0] == 1
        conn.close()

        server = HTTPServer(('127.0.0.1', 0), MockLLM)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        hk.EXTRACT_BASE_URL = f'http://127.0.0.1:{server.server_port}/v1'
        hk.EXTRACT_MODEL = 'mock-minimax'

        extracted = hk.extract_experience('已完成迁移并验证 query', '修复 SQLite FTS 结构化召回', True, None, ['terminal'])
        assert extracted['problem'].startswith('需要修复')
        assert extracted['action'].startswith('用 ALTER TABLE')
        assert extracted['root_cause'] == 'unknown'
        assert extracted['confidence'] == 0.82

        hard_cases = [
            ('process exit code 124', 'timeout'),
            ('tool returned 404 not found', 'tool_call'),
            ('JSON decode error from json.decoder', 'llm_parse'),
            ('Connection reset by peer', 'network'),
            ('RateLimit 429 exceeded', 'resource'),
            ('api key invalid 401', 'dispatch_mismatch'),
            ('DNS lookup failed', 'network'),
        ]
        for text, expected in hard_cases:
            got = hk.extract_experience(text, '失败任务', False, text, [])
            assert got['root_cause'] == expected, (text, got)

        mem_id = hk.add(
            task_type='code',
            task_text='结构化召回验证任务',
            huluwa_id=2,
            experience='占位经验',
            tools_used=['terminal'],
            duration_ms=123,
            success=True,
            fail_reason=None,
            embedding=None,
            experience_dict=extracted,
        )
        rows = hk.query('ALTER TABLE fts_text 结构化召回', task_type='code', top_k=5)
        found = [row for row in rows if int(row['id']) == mem_id]
        assert found, rows
        row = found[0]
        assert row['problem'].startswith('需要修复')
        assert row['action'].startswith('用 ALTER TABLE')
        assert 'root_cause' in row and 'confidence' in row

        print('SMOKE_OK old_migrate mock_extract hard_rules query_structured')
    finally:
        if server:
            server.shutdown()
        hk.DB_PATH = original_db
        shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == '__main__':
    main()
