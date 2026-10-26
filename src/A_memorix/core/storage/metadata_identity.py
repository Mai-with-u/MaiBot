"""由平台用户 ID 驱动的稳定身份及历史称呼映射。"""

from typing import Any, Dict, List

import time


class MetadataIdentityMixin:
    def observe_person_identity(
        self,
        *,
        person_id: str,
        platform: str,
        user_id: str,
        aliases: List[str],
        group_id: str = "",
        preferred_name: str = "",
    ) -> Dict[str, Any]:
        """只按 ID 合并身份，保留每次观察到的昵称，不用昵称认领其他用户。"""
        if not person_id or not platform.strip() or not user_id.strip():
            raise ValueError("身份映射必须包含 person_id、platform 和 user_id")
        names = list(dict.fromkeys(name.strip() for name in aliases if name.strip()))
        now = time.time()
        with self.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT platform, user_id, canonical_name FROM person_identities WHERE person_id = ?",
                (person_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != platform or existing[1] != user_id:
                    raise ValueError("person_id 已绑定其他平台用户，不能按称呼合并")
                canonical_name = str(existing[2])
            else:
                canonical_name = preferred_name.strip() or next(iter(names), user_id)
                collision = conn.execute(
                    "SELECT person_id FROM person_identities WHERE canonical_name = ?",
                    (canonical_name,),
                ).fetchone()
                if collision is not None:
                    canonical_name = f"{canonical_name} [{person_id}]"
                conn.execute(
                    "INSERT INTO person_identities VALUES (?, ?, ?, ?, ?, ?)",
                    (person_id, platform, user_id, canonical_name, now, now),
                )
            conn.execute("UPDATE person_identities SET updated_at = ? WHERE person_id = ?", (now, person_id))
            for name in list(dict.fromkeys([canonical_name, *names])):
                conn.execute(
                    """INSERT INTO person_identity_aliases(person_id, alias, group_id, first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(person_id, alias, group_id) DO UPDATE SET last_seen = excluded.last_seen""",
                    (person_id, name, group_id, now, now),
                )
        identity = self.get_person_identity(person_id)
        assert identity is not None
        return identity

    def get_person_identity(self, person_id: str) -> Dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT person_id, platform, user_id, canonical_name FROM person_identities WHERE person_id = ?",
            (person_id,),
        ).fetchone()
        if row is None:
            return None
        aliases = self._conn.execute(
            "SELECT DISTINCT alias FROM person_identity_aliases WHERE person_id = ? ORDER BY alias",
            (person_id,),
        ).fetchall()
        return {
            "person_id": row[0],
            "platform": row[1],
            "user_id": row[2],
            "canonical_name": row[3],
            "aliases": [str(item[0]) for item in aliases],
        }

    def find_person_identities(self, alias: str, *, platform: str = "") -> List[Dict[str, Any]]:
        """同名称可以对应多人；调用方不得取第一条来猜测人物身份。"""
        rows = self._conn.execute(
            """SELECT DISTINCT i.person_id FROM person_identities i
               JOIN person_identity_aliases a ON a.person_id = i.person_id
               WHERE a.alias = ? AND (? = '' OR i.platform = ?) ORDER BY i.person_id""",
            (alias.strip(), platform, platform),
        ).fetchall()
        identities: List[Dict[str, Any]] = []
        for row in rows:
            identity = self.get_person_identity(str(row[0]))
            assert identity is not None
            identities.append(identity)
        return identities
