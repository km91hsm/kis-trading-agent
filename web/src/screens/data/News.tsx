// 뉴스·공시 — GET /news(48시간·100건[·ticker]) 다음에 목록의 id·종목으로 라벨(/news/labels)·이름(/names)을, 오늘 날짜로 판정 참조
// (/decisions/spot/news-refs)를 2차 조회해 newsRows 로 합친다. 「판정에 쓰임」 칩은 첫 판정으로 링크. 표는 30행 `Pager`(?page),
// 종목·꼬리표 열은 값이 하나라도 있을 때만, 출처는 한글, 시각은 fmtWhen. 시장은 화면 머리 세그먼트(?market=).
// 종목 칩을 누르면 `?ticker=` 로 그 종목만 다시 읽고, 필터 줄에 「차트 보기」(/data?ticker=) 링크와 해제 버튼.
// 수집은 데몬 interval 잡이고, 「지금 수집」 은 큐 kind news_collect(선택 시장, 비매매)로 바로 요청한다.
// 수동 입력은 접힌 details 안 — 저장은 확인 뒤 POST /news/manual, 최근 입력은 표(시각·제목).
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Chip } from "../../components/Chip";
import { Confirm } from "../../components/Confirm";
import { Pager } from "../../components/Pager";
import { RunJobButton } from "../../components/RunJobButton";
import { Table, type Column } from "../../components/Table";
import { errorText, postJson } from "../../lib/api";
import { fmtWhen } from "../../lib/format";
import { useKstDate, useResource } from "../../lib/hooks";
import { usePaged } from "../../lib/usePaged";
import { body, failed } from "../performance/ResourceView";
import { useMarket, type Market } from "./Data.tsx";
import { Events } from "./Events";
import { newsRows, sourceLabel, USED_CHIP, type Names, type NewsLabels, type NewsRefs, type NewsResp, type NewsRow } from "./data.ts";

type ManualResp = { market: string; items: { received_at: string; title: string; url: string; note: string }[] };

export function News() {
  const [market] = useMarket();
  const [sp, setSp] = useSearchParams();
  const filterTicker = (sp.get("ticker") ?? "").trim();
  const setFilter = (t: string | null) =>
    setSp(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (t) next.set("ticker", t);
        else next.delete("ticker");
        next.delete("page");
        return next;
      },
      { replace: true },
    );
  const today = useKstDate();
  const newsParams = useMemo((): Record<string, string> => (filterTicker ? { market, hours: "48", limit: "100", ticker: filterTicker } : { market, hours: "48", limit: "100" }), [market, filterTicker]);
  const collectParams = useMemo(() => ({ market }), [market]);
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
  const paged = usePaged(rows, 30, "page");
  const hasTickers = rows.some((r) => r.tickers.length);
  const hasChips = rows.some((r) => r.chips.length);
  const filterName = filterTicker ? (names.data?.[`${market}:${filterTicker}`] ?? rows.flatMap((r) => r.tickers).find((t) => t.ticker === filterTicker)?.name ?? null) : null;
  const chartHref = filterTicker ? (market === "US" ? `/data?ticker=${filterTicker}&market=US` : `/data?ticker=${filterTicker}`) : null;
  const columns: Column<NewsRow>[] = [
    { key: "at", label: "시각", width: "12%", render: (r) => fmtWhen(r.at) },
    { key: "title", label: "제목", width: "auto", long: true, render: (r) => (r.url ? <a href={r.url} target="_blank" rel="noreferrer">{r.title}</a> : r.title) },
    ...(hasTickers
      ? [
          {
            key: "tickers",
            label: "종목",
            width: "18%",
            align: "chip" as const,
            render: (r: NewsRow) => (
              <span className="chips">
                {r.tickers.map((t) => (
                  <button key={t.ticker} type="button" className="chip" data-tone={t.ticker === filterTicker ? "ok" : "muted"} title={`${t.ticker} 뉴스만`} onClick={() => setFilter(t.ticker)}>
                    {t.name}
                  </button>
                ))}
              </span>
            ),
          },
        ]
      : []),
    ...(hasChips
      ? [
          {
            key: "chips",
            label: "꼬리표",
            width: "20%",
            align: "chip" as const,
            render: (r: NewsRow) => (
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
        ]
      : []),
    { key: "source", label: "출처", width: "12%", render: (r) => sourceLabel(r.source) },
  ];

  return (
    <>
      <div className="perf-filters">
        <span className="data-muted">
          {news.data?.asof ? `기준 ${fmtWhen(news.data.asof)} · ` : ""}데몬이 주기적으로 수집
        </span>
        <RunJobButton kind="news_collect" params={collectParams} label="지금 수집" trading={false} realAccounts={[]} />
      </div>
      {filterTicker ? (
        <p className="perf-note data-news-filter" data-news-filter>
          <span>
            종목 {filterTicker}
            {filterName && filterName !== filterTicker ? ` ${filterName}` : ""} 뉴스만
          </span>
          <Link to={chartHref!}>차트 보기</Link>
          <button type="button" className="btn btn-sm" onClick={() => setFilter(null)}>
            해제
          </button>
        </p>
      ) : null}
      {body(news, "뉴스", () => (
        <>
          {labels.error ? failed("뉴스 라벨", labels.error) : null}
          {names.error ? failed("종목 이름", names.error) : null}
          {refs.error ? failed("판정 참조", refs.error) : null}
          <Table columns={columns} rows={paged.rows} rowKey={(r) => r.id} empty="최근 뉴스 없음" />
          <Pager {...paged} />
        </>
      ))}
      <details className="data-manual-details">
        <summary>수동 입력</summary>
        <ManualForm market={market} />
      </details>
      <h3 className="perf-sub">공시</h3>
      <Events market={market} />
    </>
  );
}

type ManualItem = ManualResp["items"][number];
const MANUAL_COLUMNS: Column<ManualItem>[] = [
  { key: "received_at", label: "시각", width: "20%", render: (m) => fmtWhen(m.received_at) },
  { key: "title", label: "제목", width: "80%", long: true, render: (m) => (m.url ? <a href={m.url} target="_blank" rel="noreferrer">{m.title}</a> : m.title) },
];

function ManualForm({ market }: { market: Market }) {
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);
  const params = useMemo(() => ({ market }), [market]);
  const recent = useResource<ManualResp>("/news/manual", params, ["news"]);
  const title = text.trim().split("\n")[0] ?? "";

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
          setConfirming(true);
        }}
      >
        <textarea aria-label="기사 붙여 넣기" placeholder="첫 줄 제목, 나머지 메모" rows={4} maxLength={4000} value={text} onChange={(e) => setText(e.target.value)} />
        <input aria-label="URL" placeholder="URL(선택)" value={url} onChange={(e) => setUrl(e.target.value)} />
        <button type="submit" className="btn" disabled={busy || !text.trim()}>
          저장
        </button>
      </form>
      <Confirm
        open={confirming}
        title="수동 기사 저장"
        body={
          <p>
            {market} 시장에 「{title}」 을(를) 저장합니다. 저장한 기사는 다음 판정의 입력에 들어갑니다.
          </p>
        }
        onConfirm={() => {
          setConfirming(false);
          void save();
        }}
        onClose={() => setConfirming(false)}
      />
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
        <Table columns={MANUAL_COLUMNS} rows={recent.data.items.slice(0, 5)} rowKey={(m) => `${m.received_at}-${m.title}`} />
      ) : null}
    </>
  );
}
