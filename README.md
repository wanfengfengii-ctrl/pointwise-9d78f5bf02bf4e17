# 金衡材料试验室复检交班

白班登记带出处的台架测读，编订并冻结试片复检规程。夜班实收交班固定件，在源站关停后用实际偏差裁定放行、隔离、补测或待查。质控员凭正式号核对当时的裁决、父件和首执。

## 运行

使用 Python 3.11 标准库。以下命令从项目根目录执行。`check_project.py` 由运行平台提供，运行 `tests` 中的完整套件。

```sh
python3 -B check_project.py
python3 -B -m unittest discover -s tests -t . -v
python3 -B verify.py
python3 -B demo.py
```

演示从 `samples/shift.json` 读取交班样例，在独立临时目录建立源站、实收和报告库。样例包含 2000 nm 录入、更正、补测、再次发行以及关闭归档后的原号领取。演示最后断开源库，再展示处置单的开办、换版、迟到单实记与各版报告领取（`DISPOSITION.md`）。`verify.py` 串行运行原回归、四份规程夹具，以及迟到单和待核换版两条核验，任一失败以非零状态退出。

## 处置交班

处置（开办、换版、发单、分拣实记）另见 `DISPOSITION.md`：`report.archive.initialize_disposition_archive`、`review.service` 下的 `open_disposition`/`revise_disposition`/`issue_disposition`/`get_disposition`，以及独立分拣库上的 `sorting.SortingDesk`。

## 工作库与公开入口

调用方持有三个独立的 `sqlite3.Connection`，使用标准 `sqlite3.connect` 的默认事务设置。写入口各自管理一个事务，要求传入连接处于空闲状态。源站连接先调用 `reference.revisions.initialize_reference_store` 与 `procedure.freeze.initialize_procedure_store`。实收和归档连接分别调用 `exchange.catalog.initialize_catalog` 与 `report.archive.initialize_archive`。初始化可重复，保留既有修订、规程、实收、报告和首执。归档初始化也为该连接启用外键检查。号均为各自库内的正整数，这套交班使用一个源站、一个实收目录和一个归档。

| 入口 | 输入与结果 |
| --- | --- |
| `reference.ingest.record_reference(source, name, reading, unit, provenance, *, replaces=None)` | 首次登记用 `None`。更正传入该名称的现行修订号，成功返回含原测和归一值的新 `ReferenceRevision`。原修订可经 `reference.history` 按名与号领取。 |
| `procedure.freeze.freeze_procedure(source, draft)` | `ProcedureDraft` 传原序 `DraftRule`。成功返回带规程号及冻结规则的 `ProcedureRevision`。`load_frozen_procedure` 按号读取冻结版。 |
| `exchange.issue.issue_packet(source, procedure_id)` | 按确切冻结号发行 `V1Envelope`。 |
| `exchange.accept.receive_packet(catalog, envelope)` | 校验外层与正文后分配实收号，返回带固定件正文和父版的 `AcceptedPacket`。每次成功实收独立入册。 |
| `review.service.review_case(catalog, archive, packet_id, deviation)` | 使用指定实收件，签存后返回 `ReconciliationView`。其中含正式号、裁决、规则出处和首执。 |
| `report.history.get_report(archive, report_id)` | 从原归档快照返回相同的 `ReconciliationView`。未知正式号返回 `None`。 |

读数、实际偏差、端点、系数和限值接受 `int`、`Fraction` 或 ASCII 字符串。文本支持有符号整数、有限十进制和分母为正的分数，分数分母须非零。单位支持 `nm` 和 `μm`。`nm` 乘以 `1/1000` 精确归一。参考名、出处和规程内的规则号须为非空 UTF-8 文本。

草稿中每条 `DraftRule` 指定 `rule_id`、`RationalInterval`、`ConversionSpec`，并可选 `reference_name`、`reference_revision`、`allow_missing`。规程至少有一条规则，规则号版内唯一。省略历史修订号时按名选现行参考，缺失时须显式允许。指定历史版必须属于所给参考名且确实存在。区间 `None` 端为无界，对应闭端标记为 `False`。相等端点构成有效单点时两端都闭合。

换算为 `y = scale × deviation + offset + reference_factor × reference`，独立规则的参考因子为零。`ConversionSpec` 还指定上界 `limit` 及 `include_limit`。冻结参考值为零时照常换算。实收件中的换算依据已经绑定冻结参考。夜班按规则原序和区间开闭判定，第一次命中便结束。`pass` 和 `fail` 携带规则号与精确值，`missing_evidence` 携带规则号，`no_rule` 只携带偏差和父件。样例中偏差 5 报 `PRIMARY` 缺证，偏差 7 由 `BACKUP` 通过并得到 14，偏差 8 由 `BACKUP` 判为不通过并得到 16。补测并重新发行后，旧件仍按原口径裁定。

V1 外层由 `version`、UTF-8 JSON 文本 `body` 和该文本的 SHA-256 `digest` 构成。摘要用于核对交班内容一致性。正文中的 `procedure_id` 与每条 `rule_id` 共同构成公开规则出处。有理数以约分后的整数字符串或分数字符串传输。每条规则含 `interval` 和 `conversion`，后者为固定换算或表示缺证的 `null`。台架原测、原单位、参考名和站内出处留在源站。离线领取仅依赖归档。

非法数据和不符合更正契约的请求抛出 `ValueError`。发行或复核指向未知冻结号或实收号时抛出 `LookupError`。SQLite 错误透传给调用方，失败的签存事务整体撤销。成功后才返回可领取的正式号和首执。历史读取使用已签快照，后续源站更正和其他签存不改变已签成果。
