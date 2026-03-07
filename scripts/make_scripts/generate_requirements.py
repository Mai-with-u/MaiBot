import tomlkit


def generate_requirements(pyproject_path="pyproject.toml", output_path="requirements.txt"):
    try:
        # 读取 pyproject.toml 文件
        with open(pyproject_path, "r", encoding="utf-8") as file:
            pyproject_data = tomlkit.load(file)

        # 获取 pyproject.toml 中的 dependencies 列表
        pyproject_dependencies = set(pyproject_data.get("project", {}).get("dependencies", []))
        if not pyproject_dependencies:
            print("未找到 dependencies 部分，无法生成 requirements.txt")
            return

        # 读取 requirements.txt 文件
        try:
            with open(output_path, "r", encoding="utf-8") as file:
                requirements = {line.strip() for line in file if line.strip()}
        except FileNotFoundError:
            requirements = set()

        if extra_dependencies := requirements - pyproject_dependencies:
            print("警告: 以下依赖项存在于 requirements.txt 中，但未在 pyproject.toml 中找到:")
            for dep in extra_dependencies:
                print(f"  - {dep}")

        # 写入更新后的 requirements.txt 文件
        with open(output_path, "w", encoding="utf-8") as file:
            file.write("\n".join(sorted(pyproject_dependencies)))

        print(f"requirements.txt 文件已生成: {output_path}")
    except FileNotFoundError:
        print(f"未找到 {pyproject_path} 文件，请检查路径是否正确。")
    except Exception as e:
        print(f"发生错误: {e}")


if __name__ == "__main__":
    generate_requirements()
