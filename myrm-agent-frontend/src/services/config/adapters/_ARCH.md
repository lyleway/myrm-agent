# config/adapters/

`ConfigSyncManager` 平台适配器：本地 Tauri vs Sandbox 配置读取与批量同步。

| 文件                | 职责                                                                                                                                                                                                                                                                                                                 |
| ------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `BaseAdapter.ts`    | 适配器抽象与共享逻辑：deviceId；两个适配器共用的失败答复 `BACKEND_NOT_AVAILABLE_ERROR` / `backendUnavailableSyncResult()` 与 `isTransportFailure()`（`fetch()` 没拿到应答的判定）；带期限的请求 `fetchWithDeadlines()`（响应头 `RESPONSE_HEADERS_DEADLINE_MS`、应答体 `RESPONSE_BODY_DEADLINE_MS`，到期取消请求）与 `isLostAnswer()`（读取失败是「应答丢了」还是「应答写错了」的判定） |
| `TauriAdapter.ts`   | 桌面端本地配置 + Next 代理 5xx 处理；请求失败与应答体读不完调 `markLocalBackendUnreachable`                                                                                                                                                                                                                          |
| `SandboxAdapter.ts` | 云沙箱 CP 配置 API                                                                                                                                                                                                                                                                                                   |
| `index.ts`          | 适配器注册与工厂（白名单 barrel）                                                                                                                                                                                                                                                                                    |

## 失败接口约定

两个适配器对「后端够不着」给出同一种答复，同步层据此决定写入是保留重试还是丢弃：

- 读取（`getAll` / `get`）：只有后端明确答复才返回值，404 是「没有这一行」。后端不可用、请求失败或超时、应答体读不完、会话过期（Sandbox 的 401）一律抛错，调用方（`ensureKeyLoaded`）才能把「不知道」和「没有」分开。Sandbox 的 `getAll` 遇 401 仍返回空表，按需读取单个 key 时由 `get` 的 401 抛错补上这个区分。
- 写入（`sync`）：没送达就返回 `backendUnavailableSyncResult()`（`error: 'Backend not available'`），不抛错。Tauri：请求失败、超时、应答体读不完、数据库未就绪、Next 代理 500 / 502 / 503 / 504；Sandbox：请求失败、超时、应答体读不完、401 / 408 / 429 / 502 / 503 / 504。其余非 2xx 抛 `ConfigSyncError`：云端 500 是服务端的应用错误，原样重发不会成功，不按「不可用」重试。
- 请求失败只在 `fetch()` 调用处判定：各浏览器措辞不同（Failed to fetch / Load failed / NetworkError ...）但都是 `TypeError`，另有响应头期限到期的 `AbortError`；处理应答时的 `TypeError` 是缺陷，照常抛出，不被当成网络问题吞掉。
- 请求有两道期限，到期都取消请求（`fetchWithDeadlines()`）：响应头 10 秒（`RESPONSE_HEADERS_DEADLINE_MS`），应答体 30 秒（`RESPONSE_BODY_DEADLINE_MS`，与通用请求层 `fetchWithTimeout` 的默认值相同，从响应头到达起算）。没有应答体期限时，后端在应答中途卡住会让读取永远挂着，冲突键卡在 `syncing`，初始加载与发送链随之冻结。期限必须取消请求而不是只放弃等待：被放弃的请求仍占着连接，浏览器对同一来源只给少数几个（HTTP/1.1 下六个），后端反复卡住就会让页面里同一来源的其他请求全部排队；取消才会释放连接。应答体期限也覆盖没人读的应答体（只看状态码的 404 与 5xx）：到期同样取消。`readJson()` 在期限内读不完（`AbortError`）、或读到一半连接断开（`TypeError`）都当作后端够不着（`isLostAnswer()`：任何不是 `SyntaxError` 的失败）；读到的应答体不是合法 JSON（`SyntaxError`）是服务端缺陷，照常抛出。
