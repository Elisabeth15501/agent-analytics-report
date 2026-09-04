# -*- coding: utf-8 -*-
"""L0 · 禁止宽泛异常捕获 — 确保 P1-2 优化成果不被回退。

验收标准：
  - scripts/*.py 与 tests/*.py 中不得出现裸 except Exception（即未指定具体异常类型）
  - 允许：except (SpecificError1, SpecificError2): / except SpecificError as e:
  - 禁止：except Exception: / except Exception as e:

本测试应随 P1-2 提交，作为回归门禁。
"""

from pathlib import Path

import pytest
import allure

pytestmark = pytest.mark.regression

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
TESTS_DIR = ROOT / "tests"


@allure.feature("代码质量")
@allure.story("异常处理规范")
@allure.title("禁止宽泛 except Exception")
@allure.severity(allure.severity_level.BLOCKER)
def test_no_broad_except_exception():
    """所有 .py 文件中不得出现裸 except Exception（未指定具体类型）。"""
    violations = []

    for pyfile in list(SCRIPTS_DIR.glob("*.py")) + list(TESTS_DIR.glob("*.py")):
            lines = pyfile.read_text(encoding="utf-8").splitlines()
            for lineno, line in enumerate(lines, start=1):
                stripped = line.strip()
                # 匹配 except Exception 或 except Exception as e:
                if stripped.startswith("except Exception") or stripped.startswith("except (Exception"):
                    # 排除注释行
                    if stripped.startswith("#"):
                        continue
                    # 排除已经指定了具体类型的写法，如 except (ValueError, TypeError):
                    if "Exception" in stripped and "(" in stripped:
                        # 检查是否只包含 Exception 而没有其他具体类型
                        inner = stripped.split("(")[1].split(")")[0] if "(" in stripped else ""
                        if inner.strip() == "Exception":
                            violations.append(f"{pyfile.relative_to(ROOT)}:{lineno}: {stripped[:80]}")
                    elif "Exception" in stripped and "as " not in stripped:
                        # 裸 except Exception:
                        violations.append(f"{pyfile.relative_to(ROOT)}:{lineno}: {stripped[:80]}")
                    elif "as " in stripped:
                        # except Exception as e:
                        violations.append(f"{pyfile.relative_to(ROOT)}:{lineno}: {stripped[:80]}")

    assert not violations, (
        f"发现 {len(violations)} 处宽泛 except Exception，请替换为具体异常类型。\n"
        + "\n".join(f"  - {v}" for v in violations[:20])
    )
