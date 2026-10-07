# Phase 1 演示仓库

任务：修复 clamp 对最小值边界的处理，保持区间内和超过上限时的行为。

初始测试有 1 个失败。将返回语句改为 `return max(minimum, min(value, maximum))` 后，3 个测试全部通过。
