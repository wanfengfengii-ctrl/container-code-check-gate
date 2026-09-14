# 集装箱箱号批量校验 API（闸口放行前复算）

纯后端服务：批量复算集装箱箱号的 ISO 6346 风格校验位，让闸口在放行前
得到**可复算**的结论。手抄箱号错一位时，末位校验码对不上，服务会逐箱
标出期望校验位、实际校验位与加权和。

- 运行时：Python 3.12、FastAPI、Pydantic v2、Uvicorn
- 无数据库、无外部依赖；字符映射与加权计算逻辑见 `app/checksum.py`
- 常驻服务只有 API 一个；`verify` 是一次性验收任务（见文末）

## 校验规则（逐字实现，不做任何归一化）

每个箱号必须**恰为 11 个字符**，服务端不做大小写转换、不去除空白：

| 位置 | 1–3 | 4 | 5–10 | 11 |
| --- | --- | --- | --- | --- |
| 含义 | 箱主代码 | 设备类别码 | 顺序号 | 校验数字 |
| 取值 | 大写 `A-Z` | 仅 `U` / `J` / `Z` | 数字 `0-9` | 数字 `0-9` |

前 10 位从左到右（位置 `i = 0..9`）计算：

- 数字取原值 `0..9`；
- 字母从 `A=10` 起递增，**跳过所有 11 的倍数**，因此
  `B=12`、`K=21`、`L=23`（跳过 22）、`V=34`（跳过 33）、`Z=38`；
- 各字符值乘以 `2**i` 求和，对 11 取余；
- 余数为 `10` 时期望校验位为 `0`，其余余数即期望校验位。

完整映射：

```
A10 B12 C13 D14 E15 F16 G17 H18 I19 J20 K21 L23 M24 N25 O26
P27 Q28 R29 S30 T31 U32 V34 W35 X36 Y37 Z38
```

例：`CSQU3054383` 前 10 位加权和 `6185`，`6185 mod 11 = 3`，末位为 `3`，通过。

## 运行

### Docker Compose（只运行 API）

```bash
docker compose up --build
# 自定义宿主端口（容器内固定 8000）：
API_PORT=18080 docker compose up --build
```

- 健康检查：`GET http://localhost:${API_PORT:-8000}/health`
- 交互文档：`http://localhost:8000/docs`

### 本地直接运行

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## 请求示例

端点：`POST /api/v1/container-numbers/verify`
请求体：`{"container_numbers": [ ... ]}`，数组长度 **1 至 100**。

```bash
curl -s -X POST http://localhost:8000/api/v1/container-numbers/verify \
  -H 'Content-Type: application/json' \
  -d '{"container_numbers": ["CSQU3054383", "CSQU3054384"]}'
```

### 情形一：批次可解析（HTTP 200）——逐箱给结论

结构全部合法即进入复算；校验位对不上属于**数据问题**而非请求无法解析，
仍返回 200，由每箱 `passed` 表达：

```json
{
  "status": "ok",
  "count": 2,
  "passed_count": 1,
  "failed_count": 1,
  "results": [
    {
      "index": 0,
      "container_number": "CSQU3054383",
      "parts": {
        "owner_code": "CSQ",
        "category_identifier": "U",
        "serial_number": "305438",
        "check_digit": "3"
      },
      "weighted_sum": 6185,
      "expected_check_digit": 3,
      "actual_check_digit": 3,
      "passed": true
    },
    {
      "index": 1,
      "container_number": "CSQU3054384",
      "parts": {
        "owner_code": "CSQ",
        "category_identifier": "U",
        "serial_number": "305438",
        "check_digit": "4"
      },
      "weighted_sum": 6185,
      "expected_check_digit": 3,
      "actual_check_digit": 4,
      "passed": false
    }
  ]
}
```

`weighted_sum` 与 `expected_check_digit` 可由调用方用上文规则原样复算。
余数 10 折叠为 0 的边界样例：`AAAU000006` 加权和 `3398`，余 10，故合法
箱号为 `AAAU0000060`。

### 情形二：批次无法解析（HTTP 422, `status=invalid_batch`）——整批拒绝

批内**任一项**结构非法，即以其**最小输入索引**拒绝整批（不返回任何逐项
结果），并指出该箱号内从左到右**首个损坏字符的位置**（位置从 1 起）：

```bash
curl -s -X POST http://localhost:8000/api/v1/container-numbers/verify \
  -H 'Content-Type: application/json' \
  -d '{"container_numbers": ["CSQU3054383", "csqu3054383"]}'
```

```json
{
  "status": "invalid_batch",
  "count": 2,
  "index": 1,
  "container_number": "csqu3054383",
  "error_code": "not_uppercase_letter",
  "position": 1,
  "message": "character at position 1 must be an uppercase letter A-Z (no case normalization is applied)"
}
```

`error_code` 取值：

- `invalid_length`：长度不是 11（含前导/尾随空白，绝不做 trim），
  `position` 为越界位置；
- `not_uppercase_letter`：前 3 位出现非大写字母；
- `invalid_category_identifier`：第 4 位不是 `U/J/Z`；
- `not_digit`：第 5–11 位出现非数字。

### 请求形状错误（HTTP 422, Pydantic `detail`）

空批、超过 100 项、字段缺失/类型错误/多余字段，返回 FastAPI 标准
`{"detail": [...]}`，与上面的业务负载 `{"status": "invalid_batch", ...}`
明确区分：

```bash
curl -s -X POST .../verify -H 'Content-Type: application/json' -d '{"container_numbers": []}'
# {"detail":[{"type":"too_short", ... "min_length":1 ...}]}
```

## 测试

字符映射跳号、加权位置敏感性、全部 11 种余数边界（含余 10→0、余 0→0）、
结构首损定位、批量边界与两类 422 的区分均有覆盖：

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
```

## 一次性验收服务 `verify`

`verify` 位于 `acceptance` profile 下，**不**随 `docker compose up` 常驻；
镜像只由 `api` 构建一次，`verify` 按名称复用同一镜像（不重复声明 `build`，
避免两个服务并行构建时争抢同一镜像标签）。它等 API 健康后用内置的独立
参考实现（不复用被测代码）做端到端断言，打印每条 PASS/FAIL 并以退出码
表达结果：

```bash
# 方式一：分别构建与运行
docker compose build api
docker compose --profile acceptance run --rm verify
# 末尾输出 "ACCEPTANCE PASSED" 且退出码 0 即验收通过
docker compose down

# 方式二：一条命令完成构建、起服务、验收并按验收码退出
docker compose --profile acceptance up --build \
  --abort-on-container-exit --exit-code-from verify
```

## 目录结构

```
app/
  checksum.py     # 字符映射、加权和、期望校验位、结构判定（独立可测）
  schemas.py      # Pydantic 请求/响应模型
  main.py         # FastAPI 路由：整批结构校验 + 逐项复算
tests/
  test_checksum.py  # 映射跳号与余数边界等单元测试
  test_api.py       # API 端到端测试
scripts/
  acceptance.py     # verify 一次性验收脚本（仅用标准库）
docker-compose.yml  # 仅 api 常驻；verify 为一次性任务
Dockerfile
```
