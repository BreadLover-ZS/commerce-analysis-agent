# 02 模块 · Python 与框架基础

前置：[总览](../01-总览.md)。本篇只补读项目需要的基础，不要求先学完一整套 Python 课程。

## 1. 先用 Java 知识找到位置

| 你熟悉的概念 | 本项目对应入口 | 对照时要注意什么 |
| --- | --- | --- |
| Controller、请求参数 | FastAPI 路由、Pydantic `TaskRequest` | 路由在 `create_app` 内定义；类型校验与业务权限分开 |
| DTO、参数校验 | `BaseModel`、`Field`、`model_validator` | 能解析为合法对象，仍不代表来源和业务结论正确 |
| 实体、数据库访问 | SQLAlchemy 模型、`Repository` | `Session` 是数据库工作单元；不是登录 Session |
| 配置类 | `Settings(BaseSettings)` | 支持环境变量与 `.env`，生产环境额外拒绝不安全配置 |
| 后台消费者 | `Worker` 轮询数据库 | 当前队列是任务表，没有 MQ ACK |
| 流程编排 | `StateGraph(ResearchState)` | 节点返回状态增量，图按边推进 |
| 拦截器/回调 | Agent middleware、模型 callback | 分别观察委派和模型调用，不能混为同一层 |

这张表用于定位职责。FastAPI 的依赖注入、Python 的协程和 Spring 的线程模型并不等价，不应逐个机械翻译。

## 2. 六种语法先看懂

按顺序打开[请求 Schema](../../../app/research/schemas.py)、[任务仓储](../../../app/storage/repository.py)和[Worker](../../../app/tasks/worker.py)。

### A. `def`、`self` 与嵌套函数

`Repository.create_task(self, user, request, idempotency_key)` 是实例方法，`self` 指当前实例。调用时写 `repo.create_task(user, request, key)`。

`build_workflow` 内又定义 `plan/collect/draft` 等函数。它们使用外层的 `repo/settings/ctx/task`，这叫闭包：每次构建图时绑定当前任务所需的对象。它不是把所有用户共享成一个全局上下文。

### B. 类型提示与 Pydantic 校验

`list[str]` 表示字符串列表，`str | None` 表示可空，`Literal["demo", "live"]` 限定候选值。普通类型提示主要帮助阅读和检查；Pydantic 才会在构造模型时执行相应解析与校验。

`TaskRequest` 的字段长度、来源枚举和周期检查是一层；登录用户、会话归属、来源授权是其他层。不要因为请求通过 Schema 就省略权限校验。

### C. 字典与对象之间的转换

```python
# 项目中的常见调用形态，request 是 Pydantic 对象
payload = request.model_dump(mode="json")
value = payload["start_date"]  # JSON 兼容形式，例如日期字符串
```

`ResearchReport.model_validate(state["report"])` 则从状态字典重新得到受 Schema 约束的对象。`model_copy(update=...)` 本身不能替代重新校验；评估器在切换模式后会重新构造 `Settings` 检查 live 凭据。

### D. `with` 与 `async with`

```python
# 形态来自 Repository 的调用方式
with repo.session() as db:
    # 查询、修改
    db.commit()
```

先看 `Repository.session` 如何创建、关闭 Session，以及 SQLite 的 `BEGIN IMMEDIATE`；再看调用方的 `commit/rollback`。退出上下文不意味着所有自定义上下文都会自动提交。

`async with checkpoint_store(settings)` 负责异步检查点资源的打开与关闭；Worker 中还有租约保护连接和总时限上下文。它们解决不同问题。

### E. `async def` 与 `await`

调用异步函数先得到协程对象，执行由 `await` 或任务调度推进。`await` 等待时允许其他协程获得运行机会；它不会自动把同步数据库查询变成异步 I/O。[Python 官方协程文档](https://docs.python.org/3.12/library/asyncio-task.html)解释了调用协程和调度任务的区别。

Worker 中的三个形态必须认出来：

| 代码 | 在项目中的目的 |
| --- | --- |
| `await asyncio.to_thread(repo.claim)` | 将同步仓储调用交给线程，避免直接占住事件循环 |
| `asyncio.create_task(execute())` | 调度任务执行，与心跳协作 |
| `async with asyncio.timeout(remaining)` | 给当前执行设置剩余总时限 |

协程取消通常在可取消的等待点体现；取消 `to_thread` 的等待不保证已启动的底层线程立即停止。因此数据库写入仍要自行检查租约。

### F. 异常传播与清理

`StaleLease` 表示当前执行者失去写权限；`TaskCancelled` 表示取消状态；`BudgetExceeded` 表示调用准入受限。不能都作为“检索失败”吞掉。

在 `collect`、`draft` 中找对取消和租约异常的重新抛出，再看 Worker 的 `finally` 怎样取消并收集协程。取消传播和资源清理的背景见[Python 官方说明](https://docs.python.org/3.12/library/asyncio-task.html#task-cancellation)。

## 3. 四类“状态”不要混淆

| 名称 | 定义入口 | 含义 |
| --- | --- | --- |
| 登录状态 | `AuthSession` | 谁正在访问系统 |
| 业务任务状态 | ORM `Task.status` | 排队、执行、待复核或终态 |
| 图状态 | `ResearchState` | 当前计划、研究、报告和校验结果 |
| 浏览器状态 | React `useState/useRef` | 当前选择的任务、展示数据和轮询游标 |

`ResearchState` 是 `TypedDict`，提供字典结构的类型说明；它没有 Pydantic 的运行时校验能力。节点产出的报告仍需单独构造或校验 `ResearchReport`。

## 4. 最小阅读动作

1. 在 `TaskRequest` 找到周期限制，指出起止日期相同为何算一天。
2. 在 `Worker.process` 标出任务执行、心跳和最终清理三个区域。
3. 在 `build_workflow` 里找到 `return {"plan": ...}`，再看 `collect` 如何读取 `state["plan"]`。

## 关键问题

1. `await asyncio.to_thread(repo.claim)` 和直接调用 `repo.claim()` 有什么差别？
2. Pydantic 对象、ORM 对象和图状态字典各负责什么？
3. 工具捕获了所有异常并返回空列表，会破坏哪些取消或权限语义？

<details>
<summary>判定要点：先自己回答</summary>

应提到事件循环、同步阻塞与线程边界；请求/输出校验、数据库持久化、图内进度三种职责；租约失效和取消必须向外传播。能解释这些即可继续，不需要背完整异步 API。

</details>

下一篇：[启动与项目结构](01-启动与项目结构.md)。选读：[Pydantic 官方模型说明](https://pydantic.dev/docs/validation/latest/concepts/models/)。
