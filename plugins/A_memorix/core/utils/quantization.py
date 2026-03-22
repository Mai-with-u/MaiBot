"""
向量量化工具模块

提供向量量化与反量化功能，用于压缩存储空间。
"""

import numpy as np
from enum import Enum
from typing import Tuple, Union

from src.common.logger import get_logger

logger = get_logger("A_Memorix.Quantization")


class QuantizationType(Enum):
    """量化类型枚举"""
    FLOAT32 = "float32"  # 无量化
    INT8 = "int8"        # 标量量化（8位整数）
    PQ = "pq"            # 乘积量化（Product Quantization）


def quantize_vector(
    vector: np.ndarray,
    quant_type: QuantizationType = QuantizationType.INT8,
) -> Union[np.ndarray, Tuple[np.ndarray, np.ndarray]]:
    """
    量化向量

    Args:
        vector: 输入向量（float32）
        quant_type: 量化类型

    Returns:
        量化后的向量：
        - INT8: int8向量
        - PQ: (编码向量, 聚类中心) 元组
    """
    if quant_type == QuantizationType.FLOAT32:
        return vector.astype(np.float32)

    elif quant_type == QuantizationType.INT8:
        return _scalar_quantize_int8(vector)

    elif quant_type == QuantizationType.PQ:
        return _product_quantize(vector)

    else:
        raise ValueError(f"不支持的量化类型: {quant_type}")


def dequantize_vector(
    quantized_vector: Union[np.ndarray, Tuple[np.ndarray, np.ndarray]],
    quant_type: QuantizationType = QuantizationType.INT8,
    original_shape: Tuple[int, ...] = None,
) -> np.ndarray:
    """
    反量化向量

    Args:
        quantized_vector: 量化后的向量
        quant_type: 量化类型
        original_shape: 原始向量形状（用于PQ）

    Returns:
        反量化后的向量（float32）
    """
    if quant_type == QuantizationType.FLOAT32:
        return quantized_vector.astype(np.float32)

    elif quant_type == QuantizationType.INT8:
        return _scalar_dequantize_int8(quantized_vector)

    elif quant_type == QuantizationType.PQ:
        if not isinstance(quantized_vector, tuple):
            raise ValueError("PQ反量化需要列表/元组格式: (codes, centroids)")
        return _product_dequantize(quantized_vector[0], quantized_vector[1])

    else:
        raise ValueError(f"不支持的量化类型: {quant_type}")


def _scalar_quantize_int8(vector: np.ndarray) -> np.ndarray:
    """
    标量量化：float32 -> int8

    将向量归一化到 [0, 255] 范围，然后映射到 int8

    Args:
        vector: 输入向量

    Returns:
        量化后的 int8 向量
    """
    # 计算最小最大值
    min_val = np.min(vector)
    max_val = np.max(vector)

    # 避免除零
    if max_val == min_val:
        return np.zeros_like(vector, dtype=np.int8)

    # 归一化到 [0, 255]
    normalized = (vector - min_val) / (max_val - min_val) * 255
    
    # 映射到 [-128, 127] 并转换为 int8
    # np.round might return float, minus 128 then cast
    quantized = np.round(normalized - 128.0).astype(np.int8)

    # 存储归一化参数（用于反量化）
    # 在实际存储中，这些参数需要单独保存
    # 这里为了简单，我们使用一个全局字典来模拟
    if not hasattr(_scalar_quantize_int8, "_params"):
        _scalar_quantize_int8._params = {}

    vector_id = id(vector)
    _scalar_quantize_int8._params[vector_id] = (min_val, max_val)

    return quantized


def _scalar_dequantize_int8(quantized: np.ndarray) -> np.ndarray:
    """
    标量反量化：int8 -> float32

    Args:
        quantized: 量化后的 int8 向量

    Returns:
        反量化后的 float32 向量
    """
    # 计算归一化参数（如果提供了）
    # 在实际应用中，min_val 和 max_val 应该被保存
    if not hasattr(_scalar_dequantize_int8, "_params"):
        # 默认假设范围是 [-1, 1]
        return (quantized.astype(np.float32) + 128.0) / 255.0 * 2.0 - 1.0

    # 尝试查找参数 (这里只是演示逻辑，实际应从存储中读取)
    # return (quantized.astype(np.float32) + 128.0) / 255.0 * (max - min) + min
    return (quantized.astype(np.float32) + 128.0) / 255.0


def quantize_matrix(
    matrix: np.ndarray,
    quant_type: QuantizationType = QuantizationType.INT8,
) -> Union[np.ndarray, Tuple[np.ndarray, np.ndarray]]:
    """
    量化矩阵（批量量化向量）

    Args:
        matrix: 输入矩阵（N x D，每行是一个向量）
        quant_type: 量化类型

    Returns:
        量化后的矩阵
    """
    if quant_type == QuantizationType.FLOAT32:
        return matrix.astype(np.float32)

    elif quant_type == QuantizationType.INT8:
        # 对整个矩阵进行全局归一化
        min_val = np.min(matrix)
        max_val = np.max(matrix)

        if max_val == min_val:
            return np.zeros_like(matrix, dtype=np.int8)

        # 归一化到 [0, 255]
        normalized = (matrix - min_val) / (max_val - min_val) * 255
        quantized = np.round(normalized).astype(np.int8)

        return quantized

    else:
        raise ValueError(f"不支持的量化类型: {quant_type}")


def dequantize_matrix(
    quantized_matrix: np.ndarray,
    quant_type: QuantizationType = QuantizationType.INT8,
    min_val: float = None,
    max_val: float = None,
) -> np.ndarray:
    """
    反量化矩阵

    Args:
        quantized_matrix: 量化后的矩阵
        quant_type: 量化类型
        min_val: 归一化最小值（int8反量化需要）
        max_val: 归一化最大值（int8反量化需要）

    Returns:
        反量化后的矩阵
    """
    if quant_type == QuantizationType.FLOAT32:
        return quantized_matrix.astype(np.float32)

    elif quant_type == QuantizationType.INT8:
        # 使用提供的归一化参数反量化
        if min_val is None or max_val is None:
            # 默认假设范围是 [0, 255] -> [-1, 1]
            return quantized_matrix.astype(np.float32) / 127.0
        else:
            # 恢复到原始范围
            normalized = quantized_matrix.astype(np.float32) / 255.0
            return normalized * (max_val - min_val) + min_val

    else:
        raise ValueError(f"不支持的量化类型: {quant_type}")


def estimate_memory_reduction(
    num_vectors: int,
    dimension: int,
    from_type: QuantizationType,
    to_type: QuantizationType,
) -> Tuple[float, float]:
    """
    估算内存节省量

    Args:
        num_vectors: 向量数量
        dimension: 向量维度
        from_type: 原始量化类型
        to_type: 目标量化类型

    Returns:
        (原始大小MB, 量化后大小MB, 节省比例)
    """
    # 计算每个向量占用的字节数
    bytes_per_element = {
        QuantizationType.FLOAT32: 4,
        QuantizationType.INT8: 1,
        QuantizationType.PQ: 0.25,  # 假设压缩到1/4
    }

    original_bytes = num_vectors * dimension * bytes_per_element[from_type]
    quantized_bytes = num_vectors * dimension * bytes_per_element[to_type]

    original_mb = original_bytes / 1024 / 1024
    quantized_mb = quantized_bytes / 1024 / 1024
    reduction_ratio = (original_bytes - quantized_bytes) / original_bytes

    return original_mb, quantized_mb, reduction_ratio


def estimate_compression_stats(
    num_vectors: int,
    dimension: int,
    quant_type: QuantizationType,
) -> dict:
    """
    估算压缩统计信息

    Args:
        num_vectors: 向量数量
        dimension: 向量维度
        quant_type: 量化类型

    Returns:
        统计信息字典
    """
    original_mb, quantized_mb, ratio = estimate_memory_reduction(
        num_vectors, dimension, QuantizationType.FLOAT32, quant_type
    )

    return {
        "num_vectors": num_vectors,
        "dimension": dimension,
        "quantization_type": quant_type.value,
        "original_size_mb": round(original_mb, 2),
        "quantized_size_mb": round(quantized_mb, 2),
        "saved_mb": round(original_mb - quantized_mb, 2),
        "compression_ratio": round(ratio * 100, 2),
    }


def _product_quantize(
    vector: np.ndarray, m: int = 8, k: int = 256
) -> Tuple[np.ndarray, np.ndarray]:
    """
    乘积量化 (PQ) 简化实现

    Args:
        vector: 输入向量 (D,)
        m: 子空间数量
        k: 每个子空间的聚类中心数

    Returns:
        (编码后的向量, 聚类中心)
    """
    d = vector.shape[0]
    if d % m != 0:
        raise ValueError(f"维度 {d} 必须能被子空间数量 {m} 整除")

    ds = d // m  # 子空间维度
    codes = np.zeros(m, dtype=np.uint8)
    centroids = np.zeros((m, k, ds), dtype=np.float32)

    # 这里采用一种简化的 PQ：不进行 K-means 训练，
    # 而是预定一些量化点或针对单向量的微型聚类（实际应用中应离线训练）
    # 为了演示，我们直接将子空间切分为 k 份进行量化
    for i in range(m):
        sub_vec = vector[i * ds : (i + 1) * ds]
        # 简化：假定每个子空间的取值范围并划分
        # 实际 PQ 应使用 k-means 产生的 centroids
        # 这里为演示创建一个随机 codebook 并找到最接近的核心
        sub_min, sub_max = np.min(sub_vec), np.max(sub_vec)
        if sub_max == sub_min:
            linspace = np.zeros(k)
        else:
            linspace = np.linspace(sub_min, sub_max, k)
        
        for j in range(k):
             centroids[i, j, :] = linspace[j]
        
        # 编码：这里简化为取子空间均值找最接近的 centroid
        sub_mean = np.mean(sub_vec)
        code = np.argmin(np.abs(linspace - sub_mean))
        codes[i] = code

    return codes, centroids


def _product_dequantize(codes: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    """
    PQ 反量化

    Args:
        codes: 编码向量 (M,)
        centroids: 聚类中心 (M, K, DS)

    Returns:
        恢复后的向量 (D,)
    """
    m, k, ds = centroids.shape
    vector = np.zeros(m * ds, dtype=np.float32)

    for i in range(m):
        code = codes[i]
        vector[i * ds : (i + 1) * ds] = centroids[i, code, :]

    return vector
