def count_to_three():
    print("开始")
    yield 1
    print("中间")
    yield 2
    print("结束")
    yield 3

# 用的时候：
for x in count_to_three():
    print(f"拿到了: {x}")