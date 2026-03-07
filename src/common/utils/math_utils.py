import hashlib

def number_to_short_id(original_id: int, salt: str, length: int = 6) -> str:
    """
    将数字编号转换为短ID（不可逆）

    :param original_id: 原始数字
    :param length: 想要生成的短ID长度 (建议 4-8)
    :return: 短ID字符串
    """
    # 1. 加盐，避免简单的哈希冲突和猜测
    data = f"{original_id}{salt}".encode("utf-8")

    # 2. 计算 SHA-256 哈希
    hash_digest = hashlib.sha256(data).digest()

    # 3. 取前几个字节转换为整数
    # 为了达到需要的长度，我们可能需要取更多的字节
    num_bytes_needed = max(4, length)  # 保证足够的熵
    hash_int = int.from_bytes(hash_digest[:num_bytes_needed], byteorder="big")

    # 4. 使用 Base62 字符集编码
    characters = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    base = len(characters)

    short_id = ""
    temp_num = hash_int

    # 生成指定长度的ID
    for _ in range(length):
        short_id = characters[temp_num % base] + short_id
        temp_num //= base

    return short_id
