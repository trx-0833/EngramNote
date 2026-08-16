"""F-30 验证：clean_rules 代码块/数学块保护"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from app.services.cleaning_service import clean_rules

text = """第一章 概述

第 1 页

```python
1
2
3
for i in range(10):
    print(i)
```

公式示例：

$$
1
2
E = mc^2
$$

Page 3

正常段落文字。

42
"""

cleaned, stats = clean_rules(text)
print("--- 清洗结果 ---")
print(cleaned)
print("--- 统计 ---", stats)

# 断言：代码块内的数字行保留
assert "1\n2\n3\nfor i in range(10):" in cleaned, "代码块内数字行被误删"
# 断言：数学块内数字保留
assert "E = mc^2" in cleaned, "公式内容被误删"
assert "1\n2" in cleaned, "数学块内数字被误删"
# 断言：正文页码行被删
assert "第 1 页" not in cleaned, "页码未删除"
assert "Page 3" not in cleaned, "Page 页码未删除"
assert "42" not in cleaned, "独立数字行未删除"
print("=== F-30 验证通过：代码块/公式受保护，页码正常删除 ===")
