import os
import re

def cleanSuffix(root_dir):
    # 精准匹配末尾固定结构：_数字.png，只删除这一段，前面原名称不动
    pattern = re.compile(r"_\d+\.png$")
    for root, _, files in os.walk(root_dir):
        for file in files:
            newName = pattern.sub("", file)
            oldPath = os.path.join(root, file)
            newPath = os.path.join(root, newName)

            if oldPath != newPath and not os.path.exists(newPath):
                os.rename(oldPath, newPath)
    print("重命名完成，仅移除末尾 _0001.png / _00001.png")

if __name__ == "__main__":
    cleanSuffix(r"D:\soft\ss_ai\resources\Day28\output_batch")