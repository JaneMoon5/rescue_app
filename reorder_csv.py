import csv
import os

# 设置 CSV 文件路径（与脚本同目录）
basedir = os.path.abspath(os.path.dirname(__file__))
csv_path = os.path.join(basedir, 'questions.csv')

# 读取原始数据
with open(csv_path, 'r', encoding='utf-8-sig') as f:
    reader = csv.reader(f)
    header = next(reader)
    rows = list(reader)

# 确定最后一列的索引
last_index = len(header) - 1
# 假设最后一列就是 sort_order，将其替换为连续编号
for i, row in enumerate(rows):
    if len(row) >= last_index + 1:
        row[last_index] = str(i + 1)   # 从 1 开始编号
    else:
        # 如果行长度不够，补足后再赋值
        row.extend([''] * (last_index + 1 - len(row)))
        row[last_index] = str(i + 1)

# 写回 CSV
with open(csv_path, 'w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(header)
    writer.writerows(rows)

print(f"已完成：{csv_path} 的最后一列已按 1, 2, 3... 的顺序重新编号。")