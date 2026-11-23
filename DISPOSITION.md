# 复检报告与分拣处置

一份报告记录按哪份规程复检，一笔分拣实记记录哪张单实际执行。试片按 `specimen` 区分，开办时固定有理偏差。之后每次更正指定另一 V1 实收号，仍以同一偏差按原规程口径复核。`pass` 对应 `release`（放行），`fail` 对应 `isolate`（隔离）。`missing_evidence` 和 `no_rule` 拒绝开办或更正。

## 调用接口

先按 README.md 初始化原实收目录和报告库。新增 `report.archive.initialize_disposition_archive(archive)` 为报告库启用处置功能，可重复调用。分拣使用独立文件库。连接沿用原工程的 SQLite 默认事务设置，调用写入口时连接空闲。

归档入口位于 `review.service`。

| 调用 | 返回 |
| --- | --- |
| `open_disposition(catalog, archive, send, request_id, specimen, packet_id, deviation)` | 开办成功返回 `{"report_id": 正式号, "ticket": 处置单}`。 |
| `revise_disposition(catalog, archive, send, request_id, specimen, parent_report_id, packet_id)` | 以该片当前正式报告为父，更正成功返回 `{"status": "signed", "report_id": 新正式号}`。旧单先实记且更正改类时返回 `{"status": "conflict", "report_id": 该申请的父报告号}`。 |
| `issue_disposition(archive, send, specimen)` | 返回该片现行正式报告的处置单。 |
| `get_disposition(archive, specimen)` | 返回 `{"report_id": 当前正式号或 None, "pending": 待核请求号或 None}`。未开办且无办理中的请求返回 `None`。此查询只读报告库。 |

报告继续通过 `report.history.get_report` 领取，包含 V1 实收号、冻结规程和规则出处及原首执。

分拣入口位于 `sorting.SortingDesk(db, *, after_commit=None)`，构造时可初始化或重开分拣账。

| 调用 | 返回 |
| --- | --- |
| `desk.apply(request_id, specimen, ticket)` | 执行有资格的单，返回 `{"request_id": 实际首次实记的请求号, "ticket": 实际首次实记的原单}`。单已失效或无资格时返回 `None`。 |
| `desk.get_execution(specimen)` | 返回同形的原实记，无实记返回 `None`。 |
| `desk.exchange(message)` | 归档业务端口，调用方把 `desk.exchange` 作为 `send`。两侧自行约定消息及应答内容，用可 JSON 序列化的记录交接。 |

处置单为可 JSON 序列化的对象，公开字段有 `specimen`、`report_id` 和 `category`，可带办理所需的其他字段。分拣台收到归档交来的原单，可以在归档关停时办理。投递可迟到或重复，把原单投到别的试片须拒绝。试片号和请求号为非空 UTF-8 文本。报告号、实收号和偏差沿用原格式。固定一个归档和分拣台，归档入口顺序调用，旧单可在换版的两侧提交之间投递。实记本身就是本题的处置执行记录。

## 生效与查询

同类更正提交新报告，但未失效的旧单照常执行。分拣每片最多实记一次。同类有效单再次投递时返回原实记，实际单和其报告号保持原值。

改类和旧单实记由分拣账决定先后。若改类先定，旧单从分拣提交时即失效，回到原类也不恢复资格。若实记先定，改类拒签，后来仍可更正为同类。归档须保全待核申请的确切候选与绑定，并按已提交的分拣终局结清。待核时现版查询返回最近正式号及待核请求，暂停该片发新单和推进其他更正，原请求可续办。新报告、首执和现版一同生效。签存失败时仍能读旧版，已发生的分拣裁决可用于原号续办。

开办和更正共用归档内的请求号空间，实记请求号在分拣账内独立。重开后同号同参可续办或复领当时的结果，复领不会恢复失效单。异参、非现版的新更正、缺证、未命中和非法参数抛出 `ValueError`。未知实收号或归档操作指向未知试片时抛出 `LookupError`。这些无效请求均无新增正式报告、首执或处置实记。SQLite 错误和模拟失答的 `TimeoutError` 透传。

为核验失答，首次提交实际处置后、返回前调用 `after_commit("apply", request_id)`。首次提交改类裁决后、应答前调用 `after_commit("revise", request_id)`，包括因旧单已实记而拒签的裁决。回调可选，回调异常模拟已提交而回复丢失。接收方重开后仍须能按原请求取得已提交结局。

## 用四份规程核对换版

`disposition_fixture.py` 的 `workbench(root)` 使用临时工作目录中的独立文件，准备四份实际冻结、发行和实收的 V1 件，关掉源库后返回 `(catalog, archive, sorting, packets)`。`packets` 按 `R6`、`R8`、`I12`、`R4` 取 `AcceptedPacket`，用其 `packet_id` 调用。四件在偏差 `2` μm 时依次为放行 `6`、放行 `8`、隔离 `12`、放行 `4`。`DropReply(operation, request_id)` 在指定回调首次到达时抛出 `TimeoutError`。只运行该夹具时核验四个 V1 裁决和原报告的重开领取。

- 一片先办 R6，再用 R8 换版，然后投递 R6 原单。若实记回复丢失，重开后仍按实际 R6 单领取。此时改 I12 须拒签，R8 仍是正式版，之后同类改 R4 可以签。
- 另一片在 R6 和 R8 分别发单。先让分拣提交 I12 改类并丢答，归档仍显示 R8 待核，但 R6、R8 的单均不能实记。若随后归档写首执失败，仍读 R8 和原待核，重开后同号可签回一份 I12 报告及首执。再换 R4，两张旧单仍不能执行，R4 单可实记。旧报告及首执可按各版正式号领取。

保留原回归和原演示。扩展 `.pointwise-runtime.json`，沿用现有 `version`、`kind` 和 `setup`，将含原回归和新业务核验的命令登记为 `verify` 参数数组，将断源换版和领取的演示命令登记为 `demo` 参数数组。核验失败须以非零状态退出，演示打印实际报告、实际处置及其所据报告号。

## 本工程落地

- `report/disposition.py`：归档侧四个入口的实现；`review.service` 原样转出。
- `sorting.py`：独立分拣库 `SortingDesk`，含投递、改类裁决与单次实记。
- `disposition_fixture.py`：`workbench(root)` 与 `DropReply`；单独运行核验四个 V1 裁决和原报告重开领取。
- `disposition_scenarios.py`：上文两条换版走查的可复用实现，供核验与测试共用。
- `verify.py`：原回归（复用 `check_project.py`）+ 四份夹具 + 两条换版核验，任一失败非零退出。
- `demo.py`：保留原交班演示，再追加断源后的迟到单换版与领取演示，打印实际报告号、实记请求号与实际执行单。
- `tests/test_disposition.py`、`tests/test_sorting.py`、`tests/test_scenarios.py`：接口、账本与走查的回归。

