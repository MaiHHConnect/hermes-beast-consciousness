#!/usr/bin/env python3
"""
hive_collective_memory smoke test

验证 4 API + dispatch 集成:
- publish_lesson 写入并返回 lesson_id
- recall_lessons 按 task_type 匹配召回
- mark_lesson_used 计数更新
- lessons_to_prompt 输出含 [集体记忆] 段
- dispatch 集成验证 (解析娃输出的 ## LESSON 段)
"""

import os
import sys
import tempfile
import time

TEST_HIVE_DIR = tempfile.mkdtemp(prefix="hive_test_")
os.environ["HERMES_HOME"] = TEST_HIVE_DIR
HIVE_DIR_PATH = os.path.join(TEST_HIVE_DIR, ".hermes", "hive")
os.makedirs(HIVE_DIR_PATH, exist_ok=True)

sys.path.insert(0, "/Users/mac/Desktop/hermes-beast-consciousness/hive")
sys.path.insert(0, "/Users/mac/Desktop/hermes-beast-consciousness")
sys.path.insert(0, os.path.expanduser("~/.hermes"))  # 让 huluwa_dispatch 可导入

import hive_collective_memory as hcm


def reset_db():
    db_path = os.path.join(HIVE_DIR_PATH, "hive.db")
    if os.path.exists(db_path):
        os.remove(db_path)
    hcm.init_db()


def test_publish_lesson():
    reset_db()
    lid = hcm.publish_lesson(
        worker_id=2, task_type="code",
        task_excerpt="实现一个 Python 装饰器缓存函数结果",
        approach="用 functools.lru_cache 包装函数,加 @lru_cache(maxsize=128) 即可",
        reusable_pattern="对纯函数 + 相同输入 -> 相同输出的场景,优先用 lru_cache;对带外部依赖的函数,用显式 dict 做缓存",
        pitfalls="不要缓存 lambda 或生成器;不要对 self/cls 做 key",
        evidence="https://docs.python.org/3/library/functools.html",
        quality_score=0.85,
    )
    assert isinstance(lid, int) and lid > 0, f"publish 失败: {lid}"
    print(f"  PASS publish_lesson 写入 lesson_id={lid}")


def test_recall_lessons():
    reset_db()
    hcm.publish_lesson(worker_id=2, task_type="code",
        task_excerpt="Python 装饰器缓存",
        approach="用 functools.lru_cache",
        reusable_pattern="纯函数 + 相同输入输出 -> 优先 lru_cache")
    hcm.publish_lesson(worker_id=3, task_type="finance_realtime",
        task_excerpt="A股实时行情查询",
        approach="用 akshare 库 + 5 秒轮询",
        reusable_pattern="实时行情 5 秒轮询 + 异常重试 3 次")
    hcm.publish_lesson(worker_id=4, task_type="general",
        task_excerpt="通用文本摘要",
        approach="用 jieba 分词 + 关键词抽取",
        reusable_pattern="中文摘要: jieba + TF-IDF")

    lessons = hcm.recall_lessons("写一个 Python 缓存装饰器", task_type="code", k=3)
    assert len(lessons) > 0, "recall 失败,没找到"
    code_lessons = [l for l in lessons if l["task_type"] == "code"]
    assert len(code_lessons) >= 1, f"code 类没召回"
    print(f"  PASS recall_lessons 召回 {len(lessons)} 条, code 类 {len(code_lessons)} 条")


def test_mark_lesson_used():
    reset_db()
    lid = hcm.publish_lesson(worker_id=5, task_type="data_analysis",
        task_excerpt="Pandas 透视表",
        approach="df.pivot_table(index=x, columns=y, values=z, aggfunc=sum)",
        reusable_pattern="二维聚合 -> pivot_table, 配 aggfunc list")
    hcm.mark_lesson_used(lid, ok=True)
    hcm.mark_lesson_used(lid, ok=True)
    hcm.mark_lesson_used(lid, ok=False)
    with hcm._connect() as conn:
        row = conn.execute("SELECT use_count, success_count, fail_count, last_used_at FROM collective_lessons WHERE lesson_id=?", (lid,)).fetchone()
    assert row["use_count"] == 3, f"use_count 期望 3, 实际 {row['use_count']}"
    assert row["success_count"] == 2, f"success_count 期望 2, 实际 {row['success_count']}"
    assert row["fail_count"] == 1, f"fail_count 期望 1, 实际 {row['fail_count']}"
    assert row["last_used_at"] is not None
    print(f"  PASS mark_lesson_used: use=3 success=2 fail=1 last_used_at=已设")


def test_lessons_to_prompt():
    reset_db()
    hcm.publish_lesson(worker_id=2, task_type="code",
        task_excerpt="测试任务",
        approach="测试做法 A",
        reusable_pattern="测试模式 B")
    lessons = hcm.recall_lessons("测试", task_type="code", k=2)
    prompt = hcm.lessons_to_prompt(lessons)
    assert "集体记忆" in prompt
    assert "做法" in prompt
    assert "模式" in prompt
    assert "quality=" in prompt
    print(f"  PASS lessons_to_prompt 输出含 [集体记忆] 段 ({len(prompt)} 字符)")
    empty_prompt = hcm.lessons_to_prompt([])
    assert empty_prompt == ""
    print(f"  PASS lessons_to_prompt([]) -> 空串")


def test_dispatch_lesson_parse():
    sys.path.insert(0, "/Users/mac/Desktop/hermes-beast-consciousness/hive")
    if "hive_dispatch" in sys.modules:
        del sys.modules["hive_dispatch"]
    import hive_dispatch
    output = """这是我的答案:
```python
@lru_cache(maxsize=128)
def add(x, y):
    return x + y
```

## LESSON
approach: 用 functools.lru_cache 装饰纯函数
reusable_pattern: 纯函数 + 不可变参数 + 无副作用 -> 优先 lru_cache
pitfalls: 不要缓存 self/cls/可变对象
evidence: Python 3.11 docs
quality_score: 0.85
"""
    parsed = hive_dispatch._parse_lesson_section(output)
    assert parsed is not None
    assert "lru_cache" in parsed["approach"]
    assert "lru_cache" in parsed["reusable_pattern"]
    assert "self" in parsed["pitfalls"]
    assert parsed["quality_score"] == 0.85
    print(f"  PASS _parse_lesson_section 解析 5 字段 (quality={parsed['quality_score']})")
    no_lesson = "## Solution\n```python\nprint(1)\n```"
    parsed_none = hive_dispatch._parse_lesson_section(no_lesson)
    assert parsed_none is None
    print(f"  PASS _parse_lesson_section (无段) -> None")


def test_inject_collective_lessons():
    reset_db()
    hcm.publish_lesson(worker_id=2, task_type="code",
        task_excerpt="Python 装饰器实现缓存",
        approach="用 functools.lru_cache 装饰",
        reusable_pattern="纯函数 -> lru_cache",
        quality_score=0.8)
    sys.path.insert(0, "/Users/mac/Desktop/hermes-beast-consciousness/hive")
    if "hive_dispatch" in sys.modules:
        del sys.modules["hive_dispatch"]
    import hive_dispatch
    prompt, lessons = hive_dispatch.inject_collective_lessons(
        "写一个 Python 缓存装饰器", task_type="code", k=3
    )
    assert len(lessons) >= 1
    assert "集体记忆" in prompt
    assert "lru_cache" in prompt
    print(f"  PASS inject_collective_lessons 端到端: prompt {len(prompt)} 字符, 召回 {len(lessons)} 条")


def main():
    print("=" * 60)
    print("hive_collective_memory smoke test")
    print(f"测试目录: {TEST_HIVE_DIR}")
    print("=" * 60)
    tests = [
        ("publish_lesson 写入", test_publish_lesson),
        ("recall_lessons 召回", test_recall_lessons),
        ("mark_lesson_used 计数", test_mark_lesson_used),
        ("lessons_to_prompt 格式化", test_lessons_to_prompt),
        ("dispatch LESSON 解析", test_dispatch_lesson_parse),
        ("inject_collective_lessons 端到端", test_inject_collective_lessons),
    ]
    passed = 0
    failed = 0
    for name, fn in tests:
        print(f"\n[{name}]")
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  FAIL: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print("\n" + "=" * 60)
    print(f"结果: {passed}/{len(tests)} PASS, {failed} FAIL")
    print("=" * 60)
    import shutil
    shutil.rmtree(TEST_HIVE_DIR, ignore_errors=True)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
