from abc import ABC, abstractmethod

from typing import Self, TypeVar, Generic, TYPE_CHECKING

import copy

if TYPE_CHECKING:
    from sqlmodel import SQLModel

T = TypeVar("T", bound="SQLModel")


class BaseDataModel:
    def deepcopy(self):
        return copy.deepcopy(self)


class BaseDatabaseDataModel(ABC, Generic[T], BaseDataModel):
    @classmethod
    @abstractmethod
    def from_db_instance(cls, db_record: T) -> Self:
        """从数据库实例创建数据模型对象"""
        raise NotImplementedError

    @abstractmethod
    def to_db_instance(self) -> T:
        """将数据模型对象转换为数据库实例"""
        raise NotImplementedError
