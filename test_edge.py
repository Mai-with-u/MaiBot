from src.chat.knowledge.kg_manager import KGManager

kg = KGManager()
kg.load_from_file()

edges = kg.graph.get_edge_list()
if edges:
    e = edges[0]
    print(f"Edge tuple: {e}")
    print(f"Edge tuple type: {type(e)}")

    edge_data = kg.graph[e[0], e[1]]
    print(f"\nEdge data type: {type(edge_data)}")
    print(f"Edge data: {edge_data}")
    print(f"Has 'get' method: {hasattr(edge_data, 'get')}")
    print(f"Is dict: {isinstance(edge_data, dict)}")

    # 尝试不同的访问方式
    try:
        print(f"\nUsing []: {edge_data['weight']}")
    except Exception as e:
        print(f"Using [] failed: {e}")

    try:
        print(f"Using .get(): {edge_data.get('weight')}")
    except Exception as e:
        print(f"Using .get() failed: {e}")

    # 查看所有属性
    print(f"\nDir: {[x for x in dir(edge_data) if not x.startswith('_')]}")
