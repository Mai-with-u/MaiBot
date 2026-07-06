from src.maisaka.memory.person_profile import _format_profile_person_block, _format_profile_reference_block


def test_profile_person_block_uses_name_only_header() -> None:
    block = _format_profile_person_block(
        "千石可乐",
        "## 关系设定\n- 他之前在小红书上认识了一个妹妹\n\n## 稳定了解\n- 千石可乐曾写错key",
    )

    assert block.startswith("千石可乐：\n  ## 关系设定")
    assert "person_id" not in block
    assert "来源" not in block


def test_profile_reference_block_keeps_internal_reference_header() -> None:
    reference = _format_profile_reference_block(["千石可乐：\n  ## 稳定了解\n- 千石可乐曾写错key"])

    assert reference.startswith("【人物画像-内部参考】")
    assert "千石可乐：" in reference
