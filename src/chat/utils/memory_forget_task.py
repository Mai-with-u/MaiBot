"""
记忆遗忘任务
每5分钟进行一次遗忘检查，根据不同的遗忘阶段删除记忆
"""

import time
import random
from typing import List

from src.common.logger import get_logger
from src.common.database.database_model import ChatHistory
from src.manager.async_task_manager import AsyncTask

logger = get_logger("memory_forget_task")


class MemoryForgetTask(AsyncTask):
    """记忆遗忘任务，每5分钟执行一次"""

    def __init__(self):
        # 每5分钟执行一次（300秒）
        super().__init__(task_name="Memory Forget Task", wait_before_start=0, run_interval=300)

    async def run(self):
        """执行遗忘检查"""
        try:
            current_time = time.time()
            # logger.info("[记忆遗忘] 开始遗忘检查...")

            # 执行4个阶段的遗忘检查
            await self._forget_stage_1(current_time)
            await self._forget_stage_2(current_time)
            await self._forget_stage_3(current_time)
            await self._forget_stage_4(current_time)

            # logger.info("[记忆遗忘] 遗忘检查完成")
        except Exception as e:
            logger.error(f"[记忆遗忘] 执行遗忘检查时出错: {e}", exc_info=True)

    async def _forget_stage_1(self, current_time: float):
        """
        第一次遗忘检查：
        搜集所有：记忆还未被遗忘检查过（forget_times=0），且已经是30分钟之外的记忆
        取count最高25%和最低25%，删除，然后标记被遗忘检查次数为1
        """
        try:
            # 30分钟 = 1800秒
            time_threshold = current_time - 1800

            # 查询符合条件的记忆：forget_times=0 且 end_time < time_threshold
            candidates = list(
                ChatHistory.select().where((ChatHistory.forget_times == 0) & (ChatHistory.end_time < time_threshold))
            )

            if not candidates:
                logger.debug("[记忆遗忘-阶段1] 没有符合条件的记忆")
                return

            logger.info(f"[记忆遗忘-阶段1] 找到 {len(candidates)} 条符合条件的记忆")

            # 按count排序
            candidates.sort(key=lambda x: x.count, reverse=True)

            # 计算要删除的数量（最高25%和最低25%）
            total_count = len(candidates)
            delete_count = int(total_count * 0.25)  # 25%

            if delete_count == 0:
                logger.debug("[记忆遗忘-阶段1] 删除数量为0，跳过")
                return

            # 选择要删除的记录（处理count相同的情况：随机选择）
            to_delete = []
            to_delete.extend(self._handle_same_count_random(candidates, delete_count, "high"))
            to_delete.extend(self._handle_same_count_random(candidates, delete_count, "low"))

            # 去重（避免重复删除），使用id去重
            seen_ids = set()
            unique_to_delete = []
            for record in to_delete:
                if record.id not in seen_ids:
                    seen_ids.add(record.id)
                    unique_to_delete.append(record)
            to_delete = unique_to_delete

            # 删除记录并更新forget_times
            deleted_count = 0
            for record in to_delete:
                try:
                    record.delete_instance()
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"[记忆遗忘-阶段1] 删除记录失败: {e}")

            # 更新剩余记录的forget_times为1
            to_delete_ids = {r.id for r in to_delete}
            remaining = [r for r in candidates if r.id not in to_delete_ids]
            if remaining:
                # 批量更新
                ids_to_update = [r.id for r in remaining]
                ChatHistory.update(forget_times=1).where(ChatHistory.id.in_(ids_to_update)).execute()

            logger.info(
                f"[记忆遗忘-阶段1] 完成：删除了 {deleted_count} 条记忆，更新了 {len(remaining)} 条记忆的forget_times为1"
            )

        except Exception as e:
            logger.error(f"[记忆遗忘-阶段1] 执行失败: {e}", exc_info=True)

    async def _forget_stage_2(self, current_time: float):
        """
        第二次遗忘检查：
        搜集所有：记忆遗忘检查为1，且已经是8小时之外的记忆
        取count最高7%和最低7%，删除，然后标记被遗忘检查次数为2
        """
        try:
            # 8小时 = 28800秒
            time_threshold = current_time - 28800

            # 查询符合条件的记忆：forget_times=1 且 end_time < time_threshold
            candidates = list(
                ChatHistory.select().where((ChatHistory.forget_times == 1) & (ChatHistory.end_time < time_threshold))
            )

            if not candidates:
                logger.debug("[记忆遗忘-阶段2] 没有符合条件的记忆")
                return

            logger.info(f"[记忆遗忘-阶段2] 找到 {len(candidates)} 条符合条件的记忆")

            # 按count排序
            candidates.sort(key=lambda x: x.count, reverse=True)

            # 计算要删除的数量（最高7%和最低7%）
            total_count = len(candidates)
            delete_count = int(total_count * 0.07)  # 7%

            if delete_count == 0:
                logger.debug("[记忆遗忘-阶段2] 删除数量为0，跳过")
                return

            # 选择要删除的记录
            to_delete = []
            to_delete.extend(self._handle_same_count_random(candidates, delete_count, "high"))
            to_delete.extend(self._handle_same_count_random(candidates, delete_count, "low"))

            # 去重
            to_delete = list(set(to_delete))

            # 删除记录
            deleted_count = 0
            for record in to_delete:
                try:
                    record.delete_instance()
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"[记忆遗忘-阶段2] 删除记录失败: {e}")

            # 更新剩余记录的forget_times为2
            to_delete_ids = {r.id for r in to_delete}
            remaining = [r for r in candidates if r.id not in to_delete_ids]
            if remaining:
                ids_to_update = [r.id for r in remaining]
                ChatHistory.update(forget_times=2).where(ChatHistory.id.in_(ids_to_update)).execute()

            logger.info(
                f"[记忆遗忘-阶段2] 完成：删除了 {deleted_count} 条记忆，更新了 {len(remaining)} 条记忆的forget_times为2"
            )

        except Exception as e:
            logger.error(f"[记忆遗忘-阶段2] 执行失败: {e}", exc_info=True)

    async def _forget_stage_3(self, current_time: float):
        """
        第三次遗忘检查：
        搜集所有：记忆遗忘检查为2，且已经是48小时之外的记忆
        取count最高5%和最低5%，删除，然后标记被遗忘检查次数为3
        """
        try:
            # 48小时 = 172800秒
            time_threshold = current_time - 172800

            # 查询符合条件的记忆：forget_times=2 且 end_time < time_threshold
            candidates = list(
                ChatHistory.select().where((ChatHistory.forget_times == 2) & (ChatHistory.end_time < time_threshold))
            )

            if not candidates:
                logger.debug("[记忆遗忘-阶段3] 没有符合条件的记忆")
                return

            logger.info(f"[记忆遗忘-阶段3] 找到 {len(candidates)} 条符合条件的记忆")

            # 按count排序
            candidates.sort(key=lambda x: x.count, reverse=True)

            # 计算要删除的数量（最高5%和最低5%）
            total_count = len(candidates)
            delete_count = int(total_count * 0.05)  # 5%

            if delete_count == 0:
                logger.debug("[记忆遗忘-阶段3] 删除数量为0，跳过")
                return

            # 选择要删除的记录
            to_delete = []
            to_delete.extend(self._handle_same_count_random(candidates, delete_count, "high"))
            to_delete.extend(self._handle_same_count_random(candidates, delete_count, "low"))

            # 去重
            to_delete = list(set(to_delete))

            # 删除记录
            deleted_count = 0
            for record in to_delete:
                try:
                    record.delete_instance()
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"[记忆遗忘-阶段3] 删除记录失败: {e}")

            # 更新剩余记录的forget_times为3
            to_delete_ids = {r.id for r in to_delete}
            remaining = [r for r in candidates if r.id not in to_delete_ids]
            if remaining:
                ids_to_update = [r.id for r in remaining]
                ChatHistory.update(forget_times=3).where(ChatHistory.id.in_(ids_to_update)).execute()

            logger.info(
                f"[记忆遗忘-阶段3] 完成：删除了 {deleted_count} 条记忆，更新了 {len(remaining)} 条记忆的forget_times为3"
            )

        except Exception as e:
            logger.error(f"[记忆遗忘-阶段3] 执行失败: {e}", exc_info=True)

    async def _forget_stage_4(self, current_time: float):
        """
        第四次遗忘检查：
        搜集所有：记忆遗忘检查为3，且已经是7天之外的记忆
        取count最高2%和最低2%，删除，然后标记被遗忘检查次数为4
        """
        try:
            # 7天 = 604800秒
            time_threshold = current_time - 604800

            # 查询符合条件的记忆：forget_times=3 且 end_time < time_threshold
            candidates = list(
                ChatHistory.select().where((ChatHistory.forget_times == 3) & (ChatHistory.end_time < time_threshold))
            )

            if not candidates:
                logger.debug("[记忆遗忘-阶段4] 没有符合条件的记忆")
                return

            logger.info(f"[记忆遗忘-阶段4] 找到 {len(candidates)} 条符合条件的记忆")

            # 按count排序
            candidates.sort(key=lambda x: x.count, reverse=True)

            # 计算要删除的数量（最高2%和最低2%）
            total_count = len(candidates)
            delete_count = int(total_count * 0.02)  # 2%

            if delete_count == 0:
                logger.debug("[记忆遗忘-阶段4] 删除数量为0，跳过")
                return

            # 选择要删除的记录
            to_delete = []
            to_delete.extend(self._handle_same_count_random(candidates, delete_count, "high"))
            to_delete.extend(self._handle_same_count_random(candidates, delete_count, "low"))

            # 去重
            to_delete = list(set(to_delete))

            # 删除记录
            deleted_count = 0
            for record in to_delete:
                try:
                    record.delete_instance()
                    deleted_count += 1
                except Exception as e:
                    logger.error(f"[记忆遗忘-阶段4] 删除记录失败: {e}")

            # 更新剩余记录的forget_times为4
            to_delete_ids = {r.id for r in to_delete}
            remaining = [r for r in candidates if r.id not in to_delete_ids]
            if remaining:
                ids_to_update = [r.id for r in remaining]
                ChatHistory.update(forget_times=4).where(ChatHistory.id.in_(ids_to_update)).execute()

            logger.info(
                f"[记忆遗忘-阶段4] 完成：删除了 {deleted_count} 条记忆，更新了 {len(remaining)} 条记忆的forget_times为4"
            )

        except Exception as e:
            logger.error(f"[记忆遗忘-阶段4] 执行失败: {e}", exc_info=True)

    def _handle_same_count_random(
        self, candidates: List[ChatHistory], delete_count: int, mode: str
    ) -> List[ChatHistory]:
        """
        处理count相同的情况，随机选择要删除的记录

        Args:
            candidates: 候选记录列表（已按count排序）
            delete_count: 要删除的数量
            mode: "high" 表示选择最高count的记录，"low" 表示选择最低count的记录

        Returns:
            要删除的记录列表
        """
        if not candidates or delete_count == 0:
            return []

        to_delete = []

        if mode == "high":
            # 从最高count开始选择
            start_idx = 0
            while start_idx < len(candidates) and len(to_delete) < delete_count:
                # 找到所有count相同的记录
                current_count = candidates[start_idx].count
                same_count_records = []
                idx = start_idx
                while idx < len(candidates) and candidates[idx].count == current_count:
                    same_count_records.append(candidates[idx])
                    idx += 1

                # 如果相同count的记录数量 <= 还需要删除的数量，全部选择
                needed = delete_count - len(to_delete)
                if len(same_count_records) <= needed:
                    to_delete.extend(same_count_records)
                else:
                    # 随机选择需要的数量
                    to_delete.extend(random.sample(same_count_records, needed))

                start_idx = idx

        else:  # mode == "low"
            # 从最低count开始选择
            start_idx = len(candidates) - 1
            while start_idx >= 0 and len(to_delete) < delete_count:
                # 找到所有count相同的记录
                current_count = candidates[start_idx].count
                same_count_records = []
                idx = start_idx
                while idx >= 0 and candidates[idx].count == current_count:
                    same_count_records.append(candidates[idx])
                    idx -= 1

                # 如果相同count的记录数量 <= 还需要删除的数量，全部选择
                needed = delete_count - len(to_delete)
                if len(same_count_records) <= needed:
                    to_delete.extend(same_count_records)
                else:
                    # 随机选择需要的数量
                    to_delete.extend(random.sample(same_count_records, needed))

                start_idx = idx

        return to_delete
