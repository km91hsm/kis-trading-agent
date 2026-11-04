// 뉴스 — GET /news(48시간·100건) 다음에 목록의 id·종목으로 라벨(/news/labels)·이름(/names)을, 오늘 날짜로 판정 참조
// (/decisions/spot/news-refs)를 2차 조회해 newsRows 로 합친다. 「판정에 쓰임」 칩은 첫 판정으로 링크.
// 수집은 데몬 interval 잡이라 「지금 수집」 버튼은 없다(Ruling T12-R1) — asof 와 안내만. 수동 입력 → POST /news/manual.
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Chip } from "../../components/Chip";
import { Table, type Column } from "../../components/Table";
import { errorText, postJson } from "../../lib/api";
import { useKstDate, useResource } from "../../lib/hooks";
import { body, failed } from "../performance/ResourceView";
import { MarketSelect, type Market } from "./Data.tsx";
import { Events, when } from "./Events";
import { newsRows, USED_CHIP, type Names, type NewsLabels, type NewsRefs, type NewsResp, type NewsRow } from "./data.ts";

type ManualResp = { market: string; items: { received_at: string; title: string; url: string; note: string }[] };

export function News() {
  const [market, setMarket] = useState<Market>("KR");
  const today = useKstDate();
  const newsParams = useMemo(() => ({ market, hours: "48", limit: "100" }), [market]);
  const news = useResource<NewsResp>("/news", newsParams, ["news"]);
  const items = news.data?.items;
  const ids = useMemo(() => (items ?? []).map((n) => n.id).join(","), [items]);
  const tickers = useMemo(() => Array.from(new Set((items ?? []).flatMap((n) => n.tickers))).join(","), [items]);
  const labelParams = useMemo(() => ({ ids }), [ids]);
  const nameParams = useMemo(() => ({ tickers, market }), [tickers, market]);
  const refParams = useMemo(() => ({ date: today }), [today]);
  const labels = useResource<NewsLabels>("/news/labels", labelParams, ["news", "theme"], { enabled: !!ids });
  const names = useResource<Names>("/names", nameParams, ["accounts"], { enabled: !!tickers });
  const refs = useResource<NewsRefs>("/decisions/spot/news-refs", refParams, ["decisions"]);

  const rows = useMemo(
    () => (items ? newsRows(items, labels.data ?? {}, refs.data ?? {}, names.data ?? {}) : []),
    [items, labels.data, refs.data, names.data],
  );
  const columns: Column<NewsRow>[] = [
    { key: "at", label: "시각", width: "14%", render: (r) => when(r.at) },
    { key: "title", label: "제목", width: "36%", long: true, render: (r) => (r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.title}</a> : r.title) },
    {
      key: "tickers",
      label: "종목",
      width: "18%",
      align: "chip",
      render: (r) => (
        <span className="chips">
          {r.tickers.map((t) => (
            <Chip key={t.ticker}>{t.name}</Chip>
          ))}
        </span>
      ),
    },
    {
      key: "chips",
      label: "꼬리표",
      width: "22%",
      align: "chip",
      render: (r) => (
        <span className="chips">
          {r.chips.map((c) =>
            c === USED_CHIP && r.decisionId !== null ? (
              <Link key={c} className="chip" data-tone="ok" to={`/decisions/spot/${r.decisionId}`}>
                {c}
              </Link>
            ) : (
              <Chip key={c} tone={c === "촉매" ? "warn" : "muted"}>
                {c}
              </Chip>
            ),
          )}
        </span>
      ),
    },
    { key: "source", label: "출처", width: "10%", render: (r) => r.source },
  ];

  return (
    <>
      <div className="perf-filters">
        <MarketSelect value={market} onChange={setMarket} />
        <span className="data-muted">
          {news.data?.asof ? `기준 ${when(news.data.asof)} · ` : ""}데몬이 주기적으로 수집
        </span>
      </div>
      {body(news, "뉴스", () => (
        <>
          {labels.error ? failed("뉴스 라벨", labels.error) : null}
          {names.error ? failed("종목 이름", names.error) : null}
          {refs.error ? failed("판정 참조", refs.error) : null}
          <Table columns={columns} rows={rows} rowKey={(r) => r.id} empty="최근 뉴스 없음" />
        </>
      ))}
      <h3 className="perf-sub">수동 입력</h3>
      <ManualForm market={market} />
      <h3 className="perf-sub">이벤트(공시)</h3>
      <Events market={market} />
    </>
  );
}

function ManualForm({ market }: { market: Market }) {
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const params = useMemo(() => ({ market }), [market]);
  const recent = useResource<ManualResp>("/news/manual", params, ["news"]);

  const save = async () => {
    setBusy(true);
    setMsg(null);
    try {
      await postJson("/news/manual", { market, text, url });
      setText("");
      setUrl("");
      setMsg({ ok: true, text: "저장했습니다" });
      recent.reload();
    } catch (e) {
      setMsg({ ok: false, text: errorText(e) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <form
        className="data-manual"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
      >
        <textarea aria-label="기사 붙여 넣기" placeholder="첫 줄 제목, 나머지 메모" rows={4} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} />
        <input aria-label="URL" placeholder="URL(선택)" value={url} onChange={(e) => setUrl(e.target.value)} />
        <button type="submit" className="btn" disabled={busy || !text.trim()}>
          저장
        </button>
      </form>
      {msg ? (
        msg.ok ? (
          <p className="data-muted">{msg.text}</p>
        ) : (
          <p className="data-error" role="alert">
            {msg.text}
          </p>
        )
      ) : null}
      {recent.error ? failed("수동 입력", recent.error) : recent.data?.items.length ? (
        <ul className="data-manual-list">
          {recent.data.items.slice(0, 5).map((m, i) => (
            <li key={`${m.received_at}-${i}`}>
              <span className="data-muted">{when(m.received_at)}</span> {m.url ? <a href={m.url} target="_blank" rel="noreferrer">{m.title}</a> : m.title}
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}
