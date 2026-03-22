"""
错别字生成器 - 基于拼音和字频的中文错别字生成工具
"""

import json
import math
import os
import random
import time
import jieba

from collections import defaultdict
from pathlib import Path
from pypinyin import Style, pinyin

from src.common.logger import get_logger

logger = get_logger("typo_gen")


class ChineseTypoGenerator:
    _jieba_dict_cache = None

    def __init__(self, error_rate=0.3, min_freq=5, tone_error_rate=0.2, word_replace_rate=0.3, max_freq_diff=200):
        """
        初始化错别字生成器

        参数:
            error_rate: 单字替换概率
            min_freq: 最小字频阈值
            tone_error_rate: 声调错误概率
            word_replace_rate: 整词替换概率
            max_freq_diff: 最大允许的频率差异
        """
        self.error_rate = error_rate
        self.min_freq = min_freq
        self.tone_error_rate = tone_error_rate
        self.word_replace_rate = word_replace_rate
        self.max_freq_diff = max_freq_diff
        
        # 加载数据
        # print("正在加载汉字数据库，请稍候...")
        # logger.info("正在加载汉字数据库，请稍候...")

        self.pinyin_dict = self._create_pinyin_dict()
        self.char_frequency = self._load_or_create_char_frequency()
        self._load_jieba_dict_once()

    @classmethod
    def _load_jieba_dict_once(cls):
        """只加载一次 jieba 词典到类缓存"""
        if cls._jieba_dict_cache is not None:
            return
        
        dict_path = os.path.join(os.path.dirname(jieba.__file__), "dict.txt")
        valid_words = {}
        with open(dict_path, "r", encoding="utf-8") as f:
            for line in f:
                parts = line.strip().split()
                if len(parts) >= 2:
                    valid_words[parts[0]] = float(parts[1])
        cls._jieba_dict_cache = valid_words

    def _load_or_create_char_frequency(self):
        """
        加载或创建汉字频率字典
        """
        cache_file = Path("depends-data/char_frequency.json")

        # 如果缓存文件存在，直接加载
        if cache_file.exists():
            with open(cache_file, "r", encoding="utf-8") as f:
                return json.load(f)

        # 使用内置的词频文件
        char_freq = defaultdict(int)
        dict_path = os.path.join(os.path.dirname(jieba.__file__), "dict.txt")

        # 读取jieba的词典文件
        with open(dict_path, "r", encoding="utf-8") as f:
            for line in f:
                word, freq = line.strip().split()[:2]
                # 对词中的每个字进行频率累加
                for char in word:
                    if self._is_chinese_char(char):
                        char_freq[char] += int(freq)

        # 归一化频率值
        max_freq = max(char_freq.values())
        normalized_freq = {char: freq / max_freq * 1000 for char, freq in char_freq.items()}

        # 保存到缓存文件
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(normalized_freq, f, ensure_ascii=False, indent=2)

        return normalized_freq

    @staticmethod
    def _create_pinyin_dict():
        """
        创建拼音到汉字的映射字典
        """
        # 常用汉字范围
        chars = [chr(i) for i in range(0x4E00, 0x9FFF)]
        pinyin_dict = defaultdict(list)

        # 为每个汉字建立拼音映射
        for char in chars:
            try:
                py = pinyin(char, style=Style.TONE3)[0][0]
                pinyin_dict[py].append(char)
            except Exception:
                continue

        return pinyin_dict

    @staticmethod
    def _is_chinese_char(char):
        """
        判断是否为汉字
        """
        try:
            return "\u4e00" <= char <= "\u9fff"
        except Exception as e:
            logger.debug(str(e))
            return False

    def _get_pinyin(self, sentence):
        """
        将中文句子拆分成单个汉字并获取其拼音
        """
        # 将句子拆分成单个字符
        characters = list(sentence)

        # 获取每个字符的拼音
        result = []
        for char in characters:
            # 跳过空格和非汉字字符
            if char.isspace() or not self._is_chinese_char(char):
                continue
            # 获取拼音（数字声调）
            py = pinyin(char, style=Style.TONE3)[0][0]
            result.append((char, py))

        return result

    @staticmethod
    def _get_similar_tone_pinyin(py):
        """
        获取相似声调的拼音
        """
        # 检查拼音是否为空或无效
        if not py or len(py) < 1:
            return py

        # 如果最后一个字符不是数字，说明可能是轻声或其他特殊情况
        if not py[-1].isdigit():
            # 为非数字结尾的拼音添加数字声调1
            return f"{py}1"

        base = py[:-1]  # 去掉声调
        tone = int(py[-1])  # 获取声调

        # 处理轻声（通常用5表示）或无效声调
        if tone not in [1, 2, 3, 4]:
            return base + str(random.choice([1, 2, 3, 4]))

        # 正常处理声调
        possible_tones = [1, 2, 3, 4]
        possible_tones.remove(tone)  # 移除原声调
        new_tone = random.choice(possible_tones)  # 随机选择一个新声调
        return base + str(new_tone)

    def _calculate_replacement_probability(self, orig_freq, target_freq):
        """
        根据频率差计算替换概率
        """
        if target_freq > orig_freq:
            return 1.0  # 如果替换字频率更高，保持原有概率

        freq_diff = orig_freq - target_freq
        if freq_diff > self.max_freq_diff:
            return 0.0  # 频率差太大，不替换

        # 使用指数衰减函数计算概率
        # 频率差为0时概率为1，频率差为max_freq_diff时概率接近0
        return math.exp(-3 * freq_diff / self.max_freq_diff)

    def _get_similar_frequency_chars(self, char, py, num_candidates=5):
        """
        获取与给定字频率相近的同音字，可能包含声调错误
        """
        homophones = []

        # 有一定概率使用错误声调
        if random.random() < self.tone_error_rate:
            wrong_tone_py = self._get_similar_tone_pinyin(py)
            homophones.extend(self.pinyin_dict[wrong_tone_py])

        # 添加正确声调的同音字
        homophones.extend(self.pinyin_dict[py])

        if not homophones:
            return None

        # 获取原字的频率
        orig_freq = self.char_frequency.get(char, 0)

        # 计算所有同音字与原字的频率差，并过滤掉低频字
        freq_diff = [
            (h, self.char_frequency.get(h, 0))
            for h in homophones
            if h != char and self.char_frequency.get(h, 0) >= self.min_freq
        ]

        if not freq_diff:
            return None

        # 计算每个候选字的替换概率
        candidates_with_prob = []
        for h, freq in freq_diff:
            prob = self._calculate_replacement_probability(orig_freq, freq)
            if prob > 0:  # 只保留有效概率的候选字
                candidates_with_prob.append((h, prob))

        if not candidates_with_prob:
            return None

        # 根据概率排序
        candidates_with_prob.sort(key=lambda x: x[1], reverse=True)

        # 返回概率最高的几个字
        return [char for char, _ in candidates_with_prob[:num_candidates]]

    @staticmethod
    def _get_word_pinyin(word):
        """
        获取词语的拼音列表
        """
        return [py[0] for py in pinyin(word, style=Style.TONE3)]

    @staticmethod
    def _segment_sentence(sentence):
        """
        使用jieba分词，返回词语列表
        """
        return list(jieba.cut(sentence))

    def _get_word_homophones(self, word):
        """
        获取整个词的同音词，只返回高频的有意义词语
        """
        if len(word) == 1:
            return []

        # 获取词的拼音
        word_pinyin = self._get_word_pinyin(word)

        # 遍历所有可能的同音字组合
        candidates = []
        for py in word_pinyin:
            chars = self.pinyin_dict.get(py, [])
            if not chars:
                return []
            candidates.append(chars)

        # 生成所有可能的组合
        import itertools

        all_combinations = itertools.product(*candidates)

        valid_words = self._jieba_dict_cache
        if valid_words is None:
            self._load_jieba_dict_once()
            valid_words = self._jieba_dict_cache

        original_word_freq = valid_words.get(word, 0)
        min_word_freq = original_word_freq * 0.1  # 设置最小词频为原词频的10%

        # 过滤和计算频率
        homophones = []
        for combo in all_combinations:
            new_word = "".join(combo)
            if new_word != word and new_word in valid_words:
                new_word_freq = valid_words[new_word]
                # 只保留词频达到阈值的词
                if new_word_freq >= min_word_freq:
                    # 计算词的平均字频（考虑字频和词频）
                    char_avg_freq = sum(self.char_frequency.get(c, 0) for c in new_word) / len(new_word)
                    # 综合评分：结合词频和字频
                    combined_score = new_word_freq * 0.7 + char_avg_freq * 0.3
                    if combined_score >= self.min_freq:
                        homophones.append((new_word, combined_score))

        # 按综合分数排序并限制返回数量
        sorted_homophones = sorted(homophones, key=lambda x: x[1], reverse=True)
        return [word for word, _ in sorted_homophones[:5]]  # 限制返回前5个结果

    def create_typo_sentence(self, sentence):
        """
        创建包含同音字错误的句子，支持词语级别和字级别的替换

        参数:
            sentence: 输入的中文句子

        返回:
            typo_sentence: 包含错别字的句子
            correction_suggestion: 随机选择的一个纠正建议，返回正确的字/词
        """
        result = []
        typo_info = []
        word_typos = []  # 记录词语错误对(错词,正确词)
        char_typos = []  # 记录单字错误对(错字,正确字)
        current_pos = 0

        # 分词
        words = self._segment_sentence(sentence)

        for word in words:
            # 如果是标点符号或空格，直接添加
            if all(not self._is_chinese_char(c) for c in word):
                result.append(word)
                current_pos += len(word)
                continue

            # 获取词语的拼音
            word_pinyin = self._get_word_pinyin(word)

            # 尝试整词替换
            if len(word) > 1 and random.random() < self.word_replace_rate:
                word_homophones = self._get_word_homophones(word)
                if word_homophones:
                    typo_word = random.choice(word_homophones)
                    # 计算词的平均频率
                    orig_freq = sum(self.char_frequency.get(c, 0) for c in word) / len(word)
                    typo_freq = sum(self.char_frequency.get(c, 0) for c in typo_word) / len(typo_word)

                    # 添加到结果中
                    result.append(typo_word)
                    typo_info.append(
                        (
                            word,
                            typo_word,
                            " ".join(word_pinyin),
                            " ".join(self._get_word_pinyin(typo_word)),
                            orig_freq,
                            typo_freq,
                        )
                    )
                    word_typos.append((typo_word, word))  # 记录(错词,正确词)对
                    current_pos += len(typo_word)
                    continue

            # 如果不进行整词替换，则进行单字替换
            if len(word) == 1:
                char = word
                py = word_pinyin[0]
                if random.random() < self.error_rate:
                    similar_chars = self._get_similar_frequency_chars(char, py)
                    if similar_chars:
                        typo_char = random.choice(similar_chars)
                        typo_freq = self.char_frequency.get(typo_char, 0)
                        orig_freq = self.char_frequency.get(char, 0)
                        replace_prob = self._calculate_replacement_probability(orig_freq, typo_freq)
                        if random.random() < replace_prob:
                            result.append(typo_char)
                            typo_py = pinyin(typo_char, style=Style.TONE3)[0][0]
                            typo_info.append((char, typo_char, py, typo_py, orig_freq, typo_freq))
                            char_typos.append((typo_char, char))  # 记录(错字,正确字)对
                            current_pos += 1
                            continue
                result.append(char)
                current_pos += 1
            else:
                # 处理多字词的单字替换
                word_result = []
                for _, (char, py) in enumerate(zip(word, word_pinyin, strict=False)):
                    # 词中的字替换概率降低
                    word_error_rate = self.error_rate * (0.7 ** (len(word) - 1))

                    if random.random() < word_error_rate:
                        similar_chars = self._get_similar_frequency_chars(char, py)
                        if similar_chars:
                            typo_char = random.choice(similar_chars)
                            typo_freq = self.char_frequency.get(typo_char, 0)
                            orig_freq = self.char_frequency.get(char, 0)
                            replace_prob = self._calculate_replacement_probability(orig_freq, typo_freq)
                            if random.random() < replace_prob:
                                word_result.append(typo_char)
                                typo_py = pinyin(typo_char, style=Style.TONE3)[0][0]
                                typo_info.append((char, typo_char, py, typo_py, orig_freq, typo_freq))
                                char_typos.append((typo_char, char))  # 记录(错字,正确字)对
                                continue
                    word_result.append(char)
                result.append("".join(word_result))
                current_pos += len(word)

        # 优先从词语错误中选择，如果没有则从单字错误中选择
        correction_suggestion = None
        # 50%概率返回纠正建议
        if random.random() < 0.5:
            if word_typos:
                wrong_word, correct_word = random.choice(word_typos)
                correction_suggestion = correct_word
            elif char_typos:
                wrong_char, correct_char = random.choice(char_typos)
                correction_suggestion = correct_char

        return "".join(result), correction_suggestion

    @staticmethod
    def format_typo_info(typo_info):
        """
        格式化错别字信息

        参数:
            typo_info: 错别字信息列表

        返回:
            格式化后的错别字信息字符串
        """
        if not typo_info:
            return "未生成错别字"

        result = []
        for orig, typo, orig_py, typo_py, orig_freq, typo_freq in typo_info:
            # 判断是否为词语替换
            is_word = " " in orig_py
            if is_word:
                error_type = "整词替换"
            else:
                tone_error = orig_py[:-1] == typo_py[:-1] and orig_py[-1] != typo_py[-1]
                error_type = "声调错误" if tone_error else "同音字替换"

            result.append(
                f"原文：{orig}({orig_py}) [频率：{orig_freq:.2f}] -> "
                f"替换：{typo}({typo_py}) [频率：{typo_freq:.2f}] [{error_type}]"
            )

        return "\n".join(result)

    def set_params(self, **kwargs):
        """
        设置参数

        可设置参数:
            error_rate: 单字替换概率
            min_freq: 最小字频阈值
            tone_error_rate: 声调错误概率
            word_replace_rate: 整词替换概率
            max_freq_diff: 最大允许的频率差异
        """
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
                print(f"参数 {key} 已设置为 {value}")
            else:
                print(f"警告: 参数 {key} 不存在")


def main():
    # 创建错别字生成器实例
    typo_generator = ChineseTypoGenerator(error_rate=0.03, min_freq=7, tone_error_rate=0.02, word_replace_rate=0.3)

    # 获取用户输入
    sentence = input("请输入中文句子：")

    # 创建包含错别字的句子
    start_time = time.time()
    typo_sentence, correction_suggestion = typo_generator.create_typo_sentence(sentence)

    # 打印结果
    print("\n原句：", sentence)
    print("错字版：", typo_sentence)

    # 打印纠正建议
    if correction_suggestion:
        print("\n随机纠正建议：")
        print(f"应该改为：{correction_suggestion}")

    # 计算并打印总耗时
    end_time = time.time()
    total_time = end_time - start_time
    print(f"\n总耗时：{total_time:.2f}秒")


if __name__ == "__main__":
    main()
