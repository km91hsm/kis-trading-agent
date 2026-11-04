// 계좌 카드 — 레인·모의/실전·모드(+메모)·일시정지, NAV·당일 손익·보유 수·대기 승인. 카드 전체가 계좌별 화면 링크.
import { Link } from "react-router-dom";
import { Chip } from "../../components/Chip";
import type { AccountCard } from "./accounts.ts";

export function AccountCardView({ card }: { card: AccountCard }) {
  return (
    <Link to={card.href} className="card account-card">
      <header className="card-head">
        <h3 className="card-title">{card.id}</h3>
        <span className="chips">
          <Chip>{card.lane}</Chip>
          <Chip tone={card.live ? "live" : "muted"}>{card.envLabel}</Chip>
          {card.paused ? <Chip tone="fail">일시정지</Chip> : null}
        </span>
      </header>
      <p className="account-mode">
        모드 {card.mode}
        {card.modeNote ? <span className="account-mode-note"> · {card.modeNote}</span> : null}
      </p>
      <dl className="account-stats">
        <div>
          <dt>NAV</dt>
          <dd>{card.nav}</dd>
        </div>
        <div>
          <dt>당일 손익</dt>
          <dd>{card.dayPnl}</dd>
        </div>
        <div>
          <dt>보유</dt>
          <dd>{card.positions}종목</dd>
        </div>
        <div>
          <dt>대기 승인</dt>
          <dd>
            <Chip tone={card.pending > 0 ? "warn" : "muted"}>{card.pending}건</Chip>
          </dd>
        </div>
      </dl>
    </Link>
  );
}
