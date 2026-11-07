from xml.etree import ElementTree

import pytest

from src.webui.services.ai_help_artifacts import HelpArtifactStore, build_help_artifact_tool


def test_publish_valid_static_svg_canonicalizes_and_deduplicates() -> None:
    store = HelpArtifactStore()
    args = {
        "kind": "svg",
        "title": "Deployment",
        "content": (
            '<svg viewBox="0 0 240 100"><title>部署</title>'
            '<g transform="translate(20 20)"><rect width="100" height="40" fill="#3266bb"/>'
            '<text x="4" y="24" fill="white">A &amp; B</text></g></svg>'
        ),
    }
    artifact = store.publish(args)
    assert artifact.kind == "svg"
    assert artifact.title == "Deployment"
    root = ElementTree.fromstring(artifact.content)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert "".join(root.itertext()) == "部署A & B"
    assert store.publish(args) == artifact
    assert store.artifacts == [artifact]
    assert build_help_artifact_tool()["name"] == "publish_help_artifact"


@pytest.mark.parametrize(
    "content",
    [
        "<svg><script>secret-input</script></svg>",
        "<svg><foreignObject>secret-input</foreignObject></svg>",
        '<svg><image href="https://example.com/secret-input"/></svg>',
        '<svg><rect style="fill:url(https://example.com/secret-input)"/></svg>',
        '<svg><rect fill="url(https://example.com/secret-input)"/></svg>',
        '<svg><rect onload="secret-input"/></svg>',
        '<svg xmlns:xlink="http://www.w3.org/1999/xlink"><path xlink:href="secret-input"/></svg>',
        '<!DOCTYPE svg [<!ENTITY test "secret-input">]><svg><text>&test;</text></svg>',
        '<?xml-stylesheet href="secret-input"?><svg/>',
        '<svg xmlns="https://example.com/secret-input"/>',
        "<svg>" + "<g>" * 14 + "</g>" * 14 + "</svg>",
        "<svg>" + "<rect/>" * 181 + "</svg>",
    ],
)
def test_publish_rejects_unsafe_or_excessive_svg_without_echo(content: str) -> None:
    store = HelpArtifactStore()
    with pytest.raises(ValueError, match="SVG") as error:
        store.publish({"kind": "svg", "title": "图表", "content": content})
    assert "secret-input" not in str(error.value)
    assert store.artifacts == []


@pytest.mark.parametrize(
    "content",
    [
        "https://docs.mai-mai.org/images/status.png",
        "/api/webui/reasoning-process/image?path=data%2Fprompt_imgs%2Fdiagram.png",
    ],
)
def test_publish_accepts_https_and_reasoning_image_urls(content: str) -> None:
    artifact = HelpArtifactStore().publish({"kind": "image", "title": "Screenshot", "content": content})
    assert artifact.content == content


@pytest.mark.parametrize(
    "content",
    [
        "http://example.com/secret-input.png",
        "javascript:secret-input",
        "data:image/svg+xml,secret-input",
        "//example.com/secret-input.png",
        "https://127.0.0.1/secret-input.png",
        "https://localhost/secret-input.png",
        "https://test.internal/secret-input.png",
        "https://secret-input@example.com/p.png",
        "https://example.com:8080/secret-input.png",
        r"https://example.com/\secret-input.png",
        "/api/webui/reasoning-process/image?path=secret-input#",
        "/api/webui/reasoning-process/image?path=secret-input&path=other",
        "/api/webui/reasoning-process/image?path=",
        "/api/webui/reasoning-process/image?path=secret-input%00",
        "/api/webui/reasoning-process/image?path=secret-input&other=1",
        "/api/webui/other-image?path=secret-input",
    ],
)
def test_publish_rejects_invalid_image_urls_without_echo(content: str) -> None:
    with pytest.raises(ValueError, match="图片") as error:
        HelpArtifactStore().publish({"kind": "image", "title": "Screenshot", "content": content})
    assert "secret-input" not in str(error.value)


def test_publish_checks_schema_and_limits_artifact_count() -> None:
    store = HelpArtifactStore()
    with pytest.raises(ValueError, match="成果格式") as error:
        store.publish({"kind": "secret-input", "title": "Draft", "content": "content"})
    assert "secret-input" not in str(error.value)
    for number in range(4):
        store.publish({"kind": "markdown", "title": f"Note {number}", "content": "| A | B |\n| - | - |"})
    with pytest.raises(ValueError, match="最多发布四件"):
        store.publish({"kind": "markdown", "title": "Overflow", "content": "# Heading"})
    assert len(store.artifacts) == 4
