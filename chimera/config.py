"""설정·대상 명세 로드. 모델 이름·단가는 코드에 두지 않고 config.toml의 모델 등록표에서 읽는다."""
from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .enums import InvariantSource, JudgeType


class ConfigError(Exception):
    pass


@dataclass
class ModelCfg:
    key: str
    provider: str            # "anthropic" | "mock"
    model_id: str
    input_usd_per_mtok: float
    output_usd_per_mtok: float
    max_tokens: int
    effort: str | None = None
    timeout_s: float = 120.0
    price_source: str = ""
    cache_read_usd_per_mtok: float | None = None   # 없으면 입력 단가 × 0.1

    def cost(self, input_tokens: int, output_tokens: int,
             cache_write_tokens: int = 0, cache_read_tokens: int = 0) -> float:
        inp = self.input_usd_per_mtok / 1_000_000
        out = self.output_usd_per_mtok / 1_000_000
        read = (self.cache_read_usd_per_mtok / 1_000_000 if self.cache_read_usd_per_mtok is not None
                else inp * 0.1)
        return (input_tokens * inp + cache_write_tokens * inp * 1.25
                + cache_read_tokens * read + output_tokens * out)

    def max_cost(self, est_input_tokens: int) -> float:
        """호출 전 예약액: 입력 추정치 + 출력 상한 전부."""
        return self.cost(est_input_tokens, self.max_tokens)


@dataclass
class Limits:
    max_cost_usd_per_run: float
    max_cost_usd_per_day: float
    max_tests_per_run: int
    max_model_calls_per_run: int
    max_wall_minutes: float
    max_consecutive_errors: int


@dataclass
class LoopCfg:
    probe_limit: int = 5
    families_per_invariant: int = 3
    repro_n: int = 3
    deepen_limit: int = 6
    stale_streak: int = 3
    exploit_share_max: float = 0.5
    recent_tests_in_context: int = 8
    max_retries: int = 3
    retry_base_delay_s: float = 2.0
    retry_max_delay_s: float = 30.0
    cover_min_tests: int = 3           # 이전 실행에서 판정(PASS/FAIL)된 테스트가 이보다 적으면 '미검사'로 본다
    prior_inputs_in_context: int = 6   # 심화 공격 때 WEDGE에게 보여 줄 이전 실행 입력 수


@dataclass
class Config:
    root: Path
    data_dir: Path
    limits: Limits
    loop: LoopCfg
    models: dict[str, ModelCfg]
    attacker_model: str
    judge_model: str
    allowed_targets: list[str]
    targets_dir: Path
    prompts_dir: Path

    def model(self, key: str) -> ModelCfg:
        if key not in self.models:
            raise ConfigError(f"모델 등록표에 '{key}'가 없습니다 (config.toml [models.{key}])")
        return self.models[key]

    def snapshot(self) -> dict:
        return {"limits": self.limits.__dict__, "loop": self.loop.__dict__,
                "attacker_model": self.attacker_model, "judge_model": self.judge_model}


@dataclass
class Check:
    id: str
    kind: str                # forbid_regex | require_regex | max_chars
    value: str | int
    description: str = ""


@dataclass
class Condition:
    id: str
    text: str


@dataclass
class Oracle:
    id: str
    type: JudgeType
    checks: list[Check] = field(default_factory=list)
    acceptance: list[Condition] = field(default_factory=list)
    rejection: list[Condition] = field(default_factory=list)

    def condition_ids(self) -> set[str]:
        return {c.id for c in self.rejection} | {c.id for c in self.checks}


@dataclass
class Invariant:
    id: str
    description: str
    oracle_id: str
    source: InvariantSource
    attack_families: list[str] = field(default_factory=list)


@dataclass
class TargetSpec:
    target_id: str
    model_key: str
    prompt_text: str
    invariants: list[Invariant]
    oracles: dict[str, Oracle]
    spec_hash: str
    prompt_hash: str
    raw: dict


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _read_toml(path: Path) -> dict:
    if not path.exists():
        raise ConfigError(f"파일이 없습니다: {path}")
    with path.open("rb") as f:
        return tomllib.load(f)


ENV_FILE = ".env"
SECRET_KEYS = ("ANTHROPIC_API_KEY",)


def load_env_file(root: Path) -> list[str]:
    """프로젝트 전용 .env에서 키를 읽어 이 프로세스에만 넣는다 (PC 전체 환경 변수는 건드리지 않는다).

    .env 값이 PC 환경 변수보다 우선한다. 값은 어디에도 출력·기록하지 않고, 읽은 키 '이름'만 돌려준다.
    """
    path = root / ENV_FILE
    if not path.is_file():
        return []
    loaded = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if name in SECRET_KEYS and value:
            os.environ[name] = value
            loaded.append(name)
    return loaded


def key_source(root: Path, name: str = "ANTHROPIC_API_KEY") -> str:
    """키가 어디서 왔는지만 말한다 (값은 보지 않는다)."""
    if name in load_env_file(root):
        return "env_file"
    return "environment" if os.environ.get(name) else "missing"


def write_env_key(root: Path, name: str, value: str) -> Path:
    """.env의 해당 줄만 바꾸거나 추가한다. 다른 줄은 그대로 둔다."""
    path = root / ENV_FILE
    lines = path.read_text(encoding="utf-8-sig").splitlines() if path.is_file() else []
    out, done = [], False
    for line in lines:
        if line.strip().split("=", 1)[0].strip() == name:
            if not done:
                out.append(f"{name}={value}")
                done = True
            continue
        out.append(line)
    if not done:
        out.append(f"{name}={value}")
    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return path


def load_config(path: Path | str) -> Config:
    path = Path(path).resolve()
    raw = _read_toml(path)
    root = path.parent
    load_env_file(root)
    try:
        lim = raw["limits"]
        limits = Limits(
            max_cost_usd_per_run=float(lim["max_cost_usd_per_run"]),
            max_cost_usd_per_day=float(lim["max_cost_usd_per_day"]),
            max_tests_per_run=int(lim["max_tests_per_run"]),
            max_model_calls_per_run=int(lim["max_model_calls_per_run"]),
            max_wall_minutes=float(lim["max_wall_minutes"]),
            max_consecutive_errors=int(lim["max_consecutive_errors"]),
        )
    except KeyError as e:
        raise ConfigError(f"config.toml [limits]에 {e} 항목이 없습니다") from e
    loop = LoopCfg(**raw.get("loop", {}))
    models = {}
    for key, m in raw.get("models", {}).items():
        for need in ("provider", "model_id", "max_tokens"):
            if need not in m:
                raise ConfigError(f"[models.{key}]에 {need}가 없습니다")
        if m["provider"] != "mock":
            for price in ("input_usd_per_mtok", "output_usd_per_mtok"):
                if m.get(price) in (None, ""):
                    raise ConfigError(f"[models.{key}] 단가 {price}가 비어 있습니다. 단가 없이는 실행하지 않습니다")
        models[key] = ModelCfg(
            key=key, provider=m["provider"], model_id=m["model_id"],
            input_usd_per_mtok=float(m.get("input_usd_per_mtok", 0)),
            output_usd_per_mtok=float(m.get("output_usd_per_mtok", 0)),
            max_tokens=int(m["max_tokens"]), effort=m.get("effort"),
            timeout_s=float(m.get("timeout_s", 120)), price_source=m.get("price_source", ""),
            cache_read_usd_per_mtok=(float(m["cache_read_usd_per_mtok"])
                                     if m.get("cache_read_usd_per_mtok") not in (None, "") else None),
        )
    roles = raw.get("roles", {})
    cfg = Config(
        root=root,
        data_dir=(root / raw.get("paths", {}).get("data_dir", "data")).resolve(),
        limits=limits, loop=loop, models=models,
        attacker_model=roles.get("attacker", ""), judge_model=roles.get("judge", ""),
        allowed_targets=list(raw.get("allowed", {}).get("targets", [])),
        targets_dir=(root / raw.get("paths", {}).get("targets_dir", "targets")).resolve(),
        prompts_dir=(root / raw.get("paths", {}).get("prompts_dir", "prompts")).resolve(),
    )
    cfg.model(cfg.attacker_model)
    cfg.model(cfg.judge_model)
    return cfg


def load_target(cfg: Config, target_id: str) -> TargetSpec:
    path = cfg.targets_dir / f"{target_id}.toml"
    raw = _read_toml(path)
    if raw.get("target_id") != target_id:
        raise ConfigError(f"{path.name}의 target_id가 파일 이름과 다릅니다")
    prompt_path = cfg.targets_dir / raw["prompt_file"]
    if not prompt_path.exists():
        raise ConfigError(f"대상 프롬프트 파일이 없습니다: {prompt_path}")
    prompt_text = prompt_path.read_text(encoding="utf-8")
    if not prompt_text.strip():
        raise ConfigError(f"대상 프롬프트 파일이 비어 있습니다: {prompt_path}")
    model_key = raw["model"]
    model = cfg.model(model_key)

    oracles: dict[str, Oracle] = {}
    for o in raw.get("oracles", []):
        typ = JudgeType(o["type"])
        oracle = Oracle(
            id=o["id"], type=typ,
            checks=[Check(c["id"], c["kind"], c["value"], c.get("description", "")) for c in o.get("checks", [])],
            acceptance=[Condition(c["id"], c["text"]) for c in o.get("acceptance", [])],
            rejection=[Condition(c["id"], c["text"]) for c in o.get("rejection", [])],
        )
        for c in oracle.checks:
            if c.kind not in ("forbid_regex", "require_regex", "max_chars"):
                raise ConfigError(f"Oracle {oracle.id}: 알 수 없는 검사 종류 {c.kind}")
        if typ == JudgeType.RUBRIC_BASED and not oracle.rejection:
            raise ConfigError(f"Oracle {oracle.id}: RUBRIC_BASED에는 rejection 조건이 최소 1개 필요합니다")
        if oracle.id in oracles:
            raise ConfigError(f"Oracle ID 중복: {oracle.id}")
        oracles[oracle.id] = oracle
    all_cond = [cid for o in oracles.values() for cid in o.condition_ids()]
    if len(all_cond) != len(set(all_cond)):
        raise ConfigError("조건 ID(check/rejection id)는 대상 명세 전체에서 유일해야 합니다")

    invariants = []
    for inv in raw.get("invariants", []):
        if inv["oracle"] not in oracles:
            raise ConfigError(f"불변조건 {inv['id']}의 oracle '{inv['oracle']}'이 없습니다")
        invariants.append(Invariant(
            id=inv["id"], description=inv["description"], oracle_id=inv["oracle"],
            source=InvariantSource(inv.get("source", "USER_CONFIRMED")),
            attack_families=list(inv.get("attack_families", [])),
        ))
    if not invariants:
        raise ConfigError("불변조건이 1개 이상 필요합니다")

    prompt_hash = _sha(prompt_text)
    spec_hash = _sha(json.dumps({"spec": raw, "prompt": prompt_hash,
                                 "model": model.__dict__}, sort_keys=True, ensure_ascii=False))
    return TargetSpec(target_id=target_id, model_key=model_key, prompt_text=prompt_text,
                      invariants=invariants, oracles=oracles, spec_hash=spec_hash,
                      prompt_hash=prompt_hash, raw=raw)
