import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Activity,
  ArrowDownToLine,
  ArrowUpRight,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Database,
  Plus,
  Info,
  Loader2,
  Search,
  X,
  TriangleAlert,
} from "lucide-react";
import {
  Button,
  Button as MenuButton,
  Button as LinkButton,
} from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Progress as ProgressBar } from "@/components/ui/progress";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import {
  Select as FilterSelect,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from "@/components/ui/table";
import {
  api,
  AccessRequired,
  date,
  englishError,
  terminal,
  stageNames,
} from "./api";
import type { Job, Result, Resource, Finding, Page } from "./types";

const initial = new URLSearchParams(location.search);
const views = ["objects", "tree", "findings"] as const;
type View = (typeof views)[number];
type Filters = {
  view: View;
  family: string;
  q: string;
  bad: boolean;
  limit: number;
  page: number;
};
const defaultFilters: Filters = {
  view: views.includes(initial.get("view") as View)
    ? (initial.get("view") as View)
    : "objects",
  family: initial.get("family") || "",
  q: initial.get("q") || "",
  bad: initial.get("bad") === "1",
  limit: [25, 50, 100].includes(Number(initial.get("limit")))
    ? Number(initial.get("limit"))
    : 50,
  page: Math.max(1, Number(initial.get("page")) || 1),
};
const ripeUrl = (value: string) =>
  "https://apps.db.ripe.net/db-web-ui/query?searchtext=" +
  encodeURIComponent(value);
function RipeLink({ value, url }: { value: string; url?: string }) {
  return (
    <a
      href={url || ripeUrl(value)}
      target="_blank"
      rel="noopener noreferrer"
      className="ripe-link"
      aria-label={`Open ${value} in RIPE Database`}
      title="Open in RIPE Database"
    >
      <ArrowUpRight size={14} />
    </a>
  );
}
function OrgLinks({ handles }: { handles: string[] }) {
  return (
    <span className="org-links">
      {handles.length
        ? handles.map((handle) => (
            <a
              key={handle}
              href={ripeUrl(handle)}
              target="_blank"
              rel="noopener noreferrer"
            >
              {handle}
            </a>
          ))
        : "—"}
    </span>
  );
}
function recentlyInactiveAsn(row: Resource) {
  return (
    row.family === "asn" &&
    row.bgp_state === "none" &&
    [
      "inactive_under_three_months",
      "recently_announced",
      "younger_than_three_months",
    ].includes(row.activity || "")
  );
}
function Bgp({ row }: { row: Resource }) {
  const labels: Record<string, string> = {
    exact: "Announced",
    more: "More specifics",
    covering: "Covered",
    mixed: "Announced + more",
    none: "Not announced",
    unknown: "Unknown",
  };
  return (
    <div>
      <span
        className={`bgp-label ${recentlyInactiveAsn(row) ? "warning" : ""}`}
      >
        {row.family === "asn"
          ? {
              recently_announced:
                row.bgp_state === "none" ? "Inactive < 3 months" : "Active",
              inactive_under_three_months: "Inactive < 3 months",
              inactive_over_three_months: "Inactive > 3 months",
              younger_than_three_months: "New ASN · not announced",
              unknown_creation_date: "Unknown registration date",
              unknown: "Unknown",
            }[row.activity || ""] ||
            labels[row.bgp_state] ||
            "Unknown"
          : labels[row.bgp_state] || "Unknown"}
      </span>
      {row.origins.length > 0 && (
        <small className="block muted mono">
          {row.origins.slice(0, 2).join(", ")}
          {row.origins.length > 2 ? ` +${row.origins.length - 2} more` : ""}
        </small>
      )}
    </div>
  );
}

export default function App() {
  const [regid, setRegid] = useState("ru.ecode"),
    [fieldError, setFieldError] = useState("");
  const [jobId, setJobId] = useState(initial.get("job") || ""),
    [job, setJob] = useState<Job | null>(null),
    [result, setResult] = useState<Result | null>(null);
  const [posting, setPosting] = useState(false),
    [notice, setNotice] = useState("");
  const [loadingJob, setLoadingJob] = useState(!!initial.get("job"));
  const [filters, setFilters] = useState(defaultFilters),
    [search, setSearch] = useState(defaultFilters.q),
    [composing, setComposing] = useState(false);
  const [detail, setDetail] = useState<Resource | null>(null),
    [detailId, setDetailId] = useState<string | null>(null),
    [detailError, setDetailError] = useState("");
  const [info, setInfo] = useState<"sources" | "rules" | null>(null),
    [access, setAccess] = useState(false),
    [token, setToken] = useState(""),
    [accessError, setAccessError] = useState(""),
    [accessBusy, setAccessBusy] = useState(false),
    [showToken, setShowToken] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null),
    regidRef = useRef<HTMLInputElement>(null),
    detailRef = useRef<HTMLButtonElement | null>(null);
  const busy = posting || loadingJob || (!!job && !terminal.has(job.status));
  const showResults =
    !!result &&
    result.job_id === jobId &&
    !busy &&
    !!job &&
    ["complete", "partial"].includes(job.status);
  const change = (patch: Partial<Filters>) =>
    setFilters((old) => ({ ...old, ...patch, page: patch.page ?? 1 }));
  const handleError = (error: unknown) => {
    if (error instanceof AccessRequired) setAccess(true);
    else
      setNotice(
        error instanceof Error
          ? error.message
          : "Request failed. Please try again.",
      );
  };
  useEffect(() => {
    const p = new URLSearchParams();
    if (jobId) p.set("job", jobId);
    p.set("view", filters.view);
    if (filters.family) p.set("family", filters.family);
    if (filters.q) p.set("q", filters.q);
    if (filters.bad) p.set("bad", "1");
    if (filters.page > 1) p.set("page", String(filters.page));
    p.set("limit", String(filters.limit));
    historyReplace(p);
  }, [jobId, filters]);
  useEffect(() => {
    const restore = () => {
      const p = new URLSearchParams(location.search);
      setJobId(p.get("job") || "");
      const next: Filters = {
        view: views.includes(p.get("view") as View)
          ? (p.get("view") as View)
          : "objects",
        family: p.get("family") || "",
        q: p.get("q") || "",
        bad: p.get("bad") === "1",
        limit: [25, 50, 100].includes(Number(p.get("limit")))
          ? Number(p.get("limit"))
          : 50,
        page: Math.max(1, Number(p.get("page")) || 1),
      };
      setFilters(next);
      setSearch(next.q);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  useEffect(() => {
    if (composing || search === filters.q) return;
    const timer = setTimeout(() => change({ q: search }), 300);
    return () => clearTimeout(timer);
  }, [search, composing, filters.q]);
  useEffect(() => {
    if (!jobId) return;
    let alive = true,
      polling = false,
      finishing = false;
    const controller = new AbortController();
    setNotice("");
    setJob(null);
    setLoadingJob(true);
    const finish = async (value: Job) => {
      if (finishing || !alive) return;
      finishing = true;
      if (value.status === "complete" || value.status === "partial") {
        try {
          const meta = await api<Result>(`/api/analyses/${jobId}/result`, {
            signal: controller.signal,
          });
          if (alive) {
            setResult(meta);
            setRegid(meta.regid);
            setNotice("");
          }
        } catch (e) {
          if (alive) handleError(e);
        }
      } else setNotice(englishError(value.error));
    };
    const poll = async () => {
      if (polling || !alive) return;
      polling = true;
      try {
        const value = await api<Job>(`/api/analyses/${jobId}`, {
          signal: controller.signal,
        });
        if (alive) {
          setJob(value);
          if (terminal.has(value.status)) await finish(value);
        }
      } catch (e) {
        if (alive) handleError(e);
      } finally {
        if (alive) setLoadingJob(false);
        polling = false;
      }
    };
    void poll();
    const events = new EventSource(`/api/analyses/${jobId}/events`);
    events.addEventListener("progress", (event) => {
      try {
        const progress = JSON.parse((event as MessageEvent).data);
        if (alive) setJob((old) => (old ? { ...old, progress } : old));
      } catch {}
    });
    events.addEventListener("finished", () => {
      events.close();
      void poll();
    });
    const timer = setInterval(() => void poll(), 3000);
    return () => {
      alive = false;
      events.close();
      clearInterval(timer);
      controller.abort();
    };
  }, [jobId]);
  useEffect(() => {
    setDetail(null);
    setDetailError("");
    if (!detailId || !result) return;
    const controller = new AbortController();
    api<Resource>(
      `/api/analyses/${result.job_id}/resources/${encodeURIComponent(detailId)}`,
      { signal: controller.signal },
    )
      .then(setDetail)
      .catch((e) => {
        if (!controller.signal.aborted) setDetailError(e.message);
      });
    return () => controller.abort();
  }, [detailId, result?.job_id]);
  async function start(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const value = regid.trim().toLowerCase();
    if (!/^[a-z]{2}\.[a-z0-9][a-z0-9._-]{0,75}$/.test(value)) {
      setFieldError("Enter a valid RIPE regID, for example ru.ecode.");
      regidRef.current?.focus();
      return;
    }
    setFieldError("");
    setNotice("");
    setPosting(true);
    try {
      const next = await api<Job>("/api/analyses", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ regid: value }),
      });
      setJob(next);
      setJobId(next.id);
      if (next.id !== jobId) change({ page: 1 });
    } catch (e) {
      handleError(e);
    } finally {
      setPosting(false);
    }
  }
  function openDetail(id: string, trigger?: HTMLButtonElement) {
    detailRef.current = trigger || null;
    setDetailId(id);
  }
  function anotherAnalysis() {
    setJobId("");
    setJob(null);
    setResult(null);
    setLoadingJob(false);
    setRegid("");
    setNotice("");
    setFieldError("");
    setSearch("");
    setComposing(false);
    setFilters({
      view: "objects",
      family: "",
      q: "",
      bad: false,
      limit: 50,
      page: 1,
    });
    setDetailId(null);
    setInfo(null);
    requestAnimationFrame(() => regidRef.current?.focus());
  }
  const progress = job?.progress;
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a href="/" className="brand">
          <span className="brand-icon">
            <Database size={17} />
          </span>
          <span>
            RIPE DB Audit<span className="brand-caption">Registry audit</span>
          </span>
        </a>
        <section className="sidebar-audit-heading">
          <div>
            <h1>Resource audit</h1>
            <p>Registration and routing checks for your LIR.</p>
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <MenuButton
                variant="ghost"
                size="icon"
                aria-label="Audit details"
              >
                <Info />
              </MenuButton>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              <DropdownMenuItem onSelect={() => setInfo("rules")}>
                Audit rules
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => setInfo("sources")}>
                Sources &amp; coverage
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </section>

        {showResults && (
          <>
            <div className="sidebar-section-label">Workspace</div>
            <Button
              id="another-analysis"
              variant="outline"
              className="another-analysis"
              onClick={anotherAnalysis}
            >
              <Plus /> Another analysis
            </Button>
            <nav aria-label="Analysis views">
              {views.map((view) => (
                <a
                  key={view}
                  className={`nav-link ${filters.view === view ? "active" : ""}`}
                  href={`?job=${jobId}&view=${view}`}
                  aria-current={filters.view === view ? "page" : undefined}
                  onClick={(e) => {
                    e.preventDefault();
                    change({ view });
                    setDetailId(null);
                  }}
                >
                  {view === "objects" ? (
                    <Database size={16} />
                  ) : view === "tree" ? (
                    <Activity size={16} />
                  ) : (
                    <TriangleAlert size={16} />
                  )}
                  <span>
                    {view === "objects"
                      ? "Resources"
                      : view === "tree"
                        ? "Network tree"
                        : "Findings"}
                  </span>
                  {view === "findings" &&
                    result &&
                    result.summary.findings > 0 && (
                      <span className="nav-count">
                        {result.summary.findings}
                      </span>
                    )}
                </a>
              ))}
            </nav>
          </>
        )}
        <div className="sidebar-report">
          <section id="result-header" hidden={!showResults}>
            {result && (
              <div className="result-header">
                <div>
                  <h2 className="mono">{result.regid}</h2>
                  <span className="muted">{result.holder}</span>
                </div>
                <div className="snapshot-meta">
                  <span>{date(result.completed_at)}</span>
                  <span className="cache-expiry">
                    {result.expires_at
                      ? `${result.expires_at * 1000 > Date.now() ? "Results cached until" : "Cache expired on"} ${date(result.expires_at)}`
                      : "Not cached"}
                  </span>
                </div>
              </div>
            )}
          </section>
          <section id="summary" hidden={!showResults} className="summary">
            {result &&
              [
                ["IPv4", result.summary.ipv4],
                ["IPv6", result.summary.ipv6],
                ["ASN", result.summary.asn],
                ["Routes", result.summary.routes],
                ["Findings", result.summary.findings],
              ].map(([label, value]) => (
                <div key={label}>
                  <span>{label}</span>
                  <strong>{Number(value).toLocaleString("en-GB")}</strong>
                </div>
              ))}
          </section>
        </div>
        <div className="sidebar-foot">
          <a
            href="https://edwardcode.net"
            target="_blank"
            rel="noopener noreferrer"
          >
            © {new Date().getFullYear()} edwardcode.net
          </a>
        </div>
      </aside>
      <main>
        <div className="page-content">
          {!showResults && (
            <form
              id="analysis-form"
              onSubmit={start}
              noValidate
              className="analysis-form"
            >
              <Label htmlFor="regid">RIPE regID</Label>
              <div className="analysis-form-row">
                <Input
                  ref={regidRef}
                  id="regid"
                  name="regid"
                  value={regid}
                  disabled={busy}
                  onChange={(e) => {
                    setRegid(e.target.value);
                    setFieldError("");
                  }}
                  placeholder="ru.ecode"
                  spellCheck={false}
                  autoCapitalize="none"
                  autoComplete="off"
                  aria-invalid={!!fieldError}
                  aria-describedby="regid-error"
                />
                <Button
                  id="start"
                  type="submit"
                  disabled={busy}
                  aria-busy={busy}
                >
                  {busy ? <Loader2 className="animate-spin" /> : <Search />}
                  {posting ? "Starting…" : busy ? "Analysing…" : "Run analysis"}
                </Button>
              </div>
              <p id="regid-error" role="alert" className="field-error">
                {fieldError}
              </p>
            </form>
          )}
          <div id="notice" aria-live="polite">
            {notice && (
              <Alert variant="destructive">
                <TriangleAlert />
                <AlertTitle>Analysis unavailable</AlertTitle>
                <AlertDescription>{notice}</AlertDescription>
              </Alert>
            )}
            {showResults && result && !result.coverage.complete && (
              <Alert>
                <TriangleAlert />
                <AlertTitle>Incomplete analysis</AlertTitle>
                <AlertDescription>
                  Some sources could not be checked.{" "}
                  <button
                    className="underline"
                    onClick={() => setInfo("sources")}
                  >
                    Review source details
                  </button>{" "}
                  before acting on these results.
                </AlertDescription>
              </Alert>
            )}
          </div>
          <section
            id="progress-panel"
            hidden={!busy}
            className="progress-panel"
            aria-label="Analysis progress"
          >
            <div className="flex justify-between gap-3">
              <span className="flex items-center gap-2 text-sm font-medium">
                <Loader2 className="size-4 animate-spin" />
                {job?.regid || regid} ·{" "}
                {stageNames[progress?.stage || "queued"] || "Processing"}
              </span>
              <span className="muted mono text-xs">
                {progress?.total
                  ? `${progress.done} / ${progress.total}`
                  : progress?.objects
                    ? `${progress.objects} objects`
                    : ""}
              </span>
            </div>
            <ProgressBar
              aria-label="Current stage progress"
              value={
                progress?.total
                  ? Math.min(100, (100 * progress.done) / progress.total)
                  : undefined
              }
              className={`h-1 ${!progress?.total ? "indeterminate" : ""}`}
            />
          </section>
          {!showResults && !busy && (
            <section className="empty-state">
              <Database size={25} />
              <h2>No analysis selected</h2>
              <p>Enter a RIPE regID to analyse IPv4, IPv6 and ASN resources.</p>
            </section>
          )}
          <section id="workspace" hidden={!showResults}>
            {showResults && result && (
              <>
                <div className="workspace-heading">
                  <h2 className="text-sm font-semibold">
                    {filters.view === "objects"
                      ? "Resources"
                      : filters.view === "tree"
                        ? "Network tree"
                        : "Findings"}
                  </h2>
                  <LinkButton asChild variant="outline" size="sm">
                    <a href={`/api/analyses/${result.job_id}/export.csv`}>
                      <ArrowDownToLine /> Export CSV
                    </a>
                  </LinkButton>
                </div>
                <div id="data-controls" className="data-controls">
                  <div className="search-wrap">
                    <Search size={15} />
                    <Input
                      ref={searchRef}
                      id="search"
                      value={search}
                      onChange={(e) => setSearch(e.target.value)}
                      onCompositionStart={() => setComposing(true)}
                      onCompositionEnd={() => setComposing(false)}
                      placeholder="Search prefix, ORG or ASN…"
                      aria-label="Search resources"
                    />
                    {search && (
                      <Button
                        id="clear-search"
                        variant="ghost"
                        size="icon"
                        aria-label="Clear search"
                        onClick={() => {
                          setSearch("");
                          change({ q: "" });
                          searchRef.current?.focus();
                        }}
                      >
                        <X />
                      </Button>
                    )}
                  </div>
                  <FilterSelect
                    value={filters.family || "all"}
                    onValueChange={(value) =>
                      change({ family: value === "all" ? "" : value })
                    }
                  >
                    <SelectTrigger
                      aria-label="Resource family"
                      className="family-select"
                    >
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {[
                        ["all", "All resources"],
                        ["ipv4", "IPv4"],
                        ["ipv6", "IPv6"],
                        ["asn", "ASN"],
                      ].map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </FilterSelect>
                  {filters.view !== "findings" && (
                    <Button
                      variant={filters.bad ? "secondary" : "outline"}
                      size="sm"
                      aria-pressed={filters.bad}
                      onClick={() => change({ bad: !filters.bad })}
                    >
                      <TriangleAlert /> With findings
                    </Button>
                  )}
                </div>
                <DataView
                  key={`${result.job_id}:${JSON.stringify(filters)}`}
                  result={result}
                  filters={filters}
                  change={change}
                  openDetail={openDetail}
                />
              </>
            )}
          </section>
        </div>
      </main>
      <Sheet
        open={!!detailId}
        onOpenChange={(open) => {
          if (!open) setDetailId(null);
        }}
      >
        <SheetContent
          id="resource-panel"
          className="detail-sheet"
          onCloseAutoFocus={(e) => {
            e.preventDefault();
            detailRef.current?.focus();
          }}
        >
          <SheetHeader>
            <SheetTitle className="detail-title mono">
              {detail?.resource || "Resource details"}
            </SheetTitle>
            <SheetDescription>
              {detail?.netname || "Registration, routing and audit evidence"}
            </SheetDescription>
          </SheetHeader>
          <div className="sheet-body">
            {detailError ? (
              <Alert variant="destructive">
                <AlertDescription>{detailError}</AlertDescription>
              </Alert>
            ) : !detail ? (
              <p role="status" className="muted flex items-center gap-2">
                <Loader2 className="size-4 animate-spin" /> Loading resource…
              </p>
            ) : (
              <ResourceDetails row={detail} />
            )}
          </div>
        </SheetContent>
      </Sheet>
      <Sheet
        open={!!info}
        onOpenChange={(open) => {
          if (!open) setInfo(null);
        }}
      >
        <SheetContent className="detail-sheet">
          <SheetHeader>
            <SheetTitle>
              {info === "rules" ? "Audit rules" : "Sources & coverage"}
            </SheetTitle>
            <SheetDescription>
              {info === "rules"
                ? "Automatic checks and their limits"
                : "Public RIPE registration and routing data"}
            </SheetDescription>
          </SheetHeader>
          <div className="sheet-body">
            {info === "rules" ? (
              <Rules />
            ) : (
              <>
                <p className="text-sm">
                  Allocations are discovered by regID. Related ASN and PI
                  resources are linked through ORG and sponsoring-org. An
                  organisation may belong to more than one LIR; this is a public
                  inventory.
                </p>
                {result && (
                  <>
                    <dl className="detail-list">
                      <dt>Snapshot</dt>
                      <dd>{date(result.completed_at)}</dd>
                      <dt>BGP snapshot</dt>
                      <dd>{date(result.bgp_snapshot)}</dd>
                      <dt>Cache expires</dt>
                      <dd>{date(result.expires_at)}</dd>
                      <dt>Coverage</dt>
                      <dd>
                        {result.coverage.complete ? "Complete" : "Incomplete"}
                      </dd>
                    </dl>
                    {result.coverage.warnings.length > 0 && (
                      <>
                        <h3>Source warnings</h3>
                        {result.coverage.warnings.map((warning, i) => (
                          <p className="text-sm" key={i}>
                            {warning.code.replaceAll("_", " ")}
                            {!/[А-Яа-я]/.test(warning.message) &&
                              `: ${warning.message}`}
                          </p>
                        ))}
                      </>
                    )}
                    <h3>Source evidence ({result.sources.length})</h3>
                    <div className="source-list">
                      {result.sources.map((source, i) => (
                        <div key={i}>
                          <a
                            href={source.url}
                            target="_blank"
                            rel="noopener noreferrer"
                          >
                            {source.url}
                          </a>
                          <small>{date(source.retrieved_at)}</small>
                        </div>
                      ))}
                    </div>
                  </>
                )}
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>
      <Sheet open={access} onOpenChange={setAccess}>
        <SheetContent>
          <SheetHeader>
            <SheetTitle>Sign in</SheetTitle>
            <SheetDescription>
              This instance requires an access token.
            </SheetDescription>
          </SheetHeader>
          <form
            noValidate
            className="sheet-body space-y-4"
            onSubmit={async (e) => {
              e.preventDefault();
              setAccessBusy(true);
              setAccessError("");
              try {
                await api("/api/session", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ token }),
                });
                setToken("");
                setAccess(false);
                if (jobId) {
                  const value = await api<Job>(`/api/analyses/${jobId}`);
                  setJob(value);
                  if (["complete", "partial"].includes(value.status))
                    setResult(
                      await api<Result>(`/api/analyses/${jobId}/result`),
                    );
                }
              } catch (error) {
                setAccessError(
                  error instanceof AccessRequired
                    ? "Invalid access token."
                    : englishError((error as Error).message),
                );
              } finally {
                setAccessBusy(false);
              }
            }}
          >
            <Label htmlFor="access-token">Access token</Label>
            <Input
              id="access-token"
              type={showToken ? "text" : "password"}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              autoComplete="current-password"
              aria-describedby="access-error"
            />
            <Button
              type="button"
              variant="outline"
              aria-pressed={showToken}
              onClick={() => setShowToken(!showToken)}
            >
              {showToken ? "Hide token" : "Show token"}
            </Button>
            <Button disabled={accessBusy} type="submit">
              {accessBusy ? "Signing in…" : "Sign in"}
            </Button>
            <p id="access-error" role="alert" className="field-error">
              {accessError}
            </p>
          </form>
        </SheetContent>
      </Sheet>
    </div>
  );
}
function historyReplace(params: URLSearchParams) {
  window.history.replaceState(null, "", "?" + params);
}

function DataView({
  result,
  filters,
  change,
  openDetail,
}: {
  result: Result;
  filters: Filters;
  change: (patch: Partial<Filters>) => void;
  openDetail: (id: string, trigger?: HTMLButtonElement) => void;
}) {
  const [page, setPage] = useState<Page<Resource | Finding> | null>(null),
    [error, setError] = useState(""),
    [retry, setRetry] = useState(0);
  const [expanded, setExpanded] = useState(new Set<string>()),
    [children, setChildren] = useState<Record<string, Page<Resource>>>({}),
    [loading, setLoading] = useState(new Set<string>());
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const isTree = filters.view === "tree" && !filters.q && !filters.bad;
  useEffect(() => {
    setError("");
    const controller = new AbortController();
    const params = new URLSearchParams({
      offset: String((filters.page - 1) * filters.limit),
      limit: String(filters.limit),
      q: filters.q,
      family: filters.family,
    });
    if (filters.view !== "findings") {
      params.set("bad", String(filters.bad));
      if (filters.view === "tree") params.set("include_bgp", "true");
      if (isTree) params.set("parent", "");
    }
    api<Page<Resource | Finding>>(
      `/api/analyses/${result.job_id}/${filters.view === "findings" ? "findings" : "resources"}?${params}`,
      { signal: controller.signal },
    )
      .then((data) => {
        if (data.total > 0 && !data.items.length && filters.page > 1) {
          change({ page: Math.ceil(data.total / filters.limit) });
          return;
        }
        setPage(data);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(e.message);
      });
    return () => controller.abort();
  }, [result.job_id, filters, retry]);
  async function loadChildren(row: Resource, more = false) {
    setLoading((old) => new Set(old).add(row.id));
    try {
      const offset = more ? children[row.id]?.items.length || 0 : 0;
      const p = new URLSearchParams({
        parent: row.id,
        include_bgp: "true",
        limit: "200",
        offset: String(offset),
        family: filters.family,
      });
      const data = await api<Page<Resource>>(
        `/api/analyses/${result.job_id}/resources?${p}`,
      );
      if (alive.current) {
        setChildren((old) => ({
          ...old,
          [row.id]: {
            ...data,
            items: more
              ? [...(old[row.id]?.items || []), ...data.items]
              : data.items,
          },
        }));
        setExpanded((old) => new Set(old).add(row.id));
      }
    } catch (e) {
      if (alive.current) setError((e as Error).message);
    } finally {
      if (alive.current)
        setLoading((old) => {
          const next = new Set(old);
          next.delete(row.id);
          return next;
        });
    }
  }
  function toggle(row: Resource) {
    if (expanded.has(row.id))
      setExpanded((old) => {
        const next = new Set(old);
        next.delete(row.id);
        return next;
      });
    else if (children[row.id]) setExpanded((old) => new Set(old).add(row.id));
    else void loadChildren(row);
  }
  function resourceRows(rows: Resource[], depth = 0): React.ReactNode {
    return rows.map((row) => (
      <ReactFragment key={row.id}>
        <TableRow
          className={
            row.finding_count
              ? "has-findings"
              : recentlyInactiveAsn(row)
                ? "recent-inactive-asn"
                : ""
          }
        >
          <TableCell>
            <div
              className="resource-cell"
              style={{ paddingLeft: isTree ? Math.min(depth, 10) * 20 : 0 }}
            >
              {isTree &&
                (row.children_count > 0 ? (
                  <Button
                    size="icon"
                    variant="ghost"
                    className="tree-toggle size-6"
                    aria-label={`${expanded.has(row.id) ? "Collapse" : "Expand"} ${row.resource}`}
                    aria-expanded={expanded.has(row.id)}
                    onClick={() => toggle(row)}
                    disabled={loading.has(row.id)}
                  >
                    {loading.has(row.id) ? (
                      <Loader2 className="animate-spin" />
                    ) : expanded.has(row.id) ? (
                      <ChevronDown />
                    ) : (
                      <ChevronRight />
                    )}
                  </Button>
                ) : (
                  <span className="size-6 shrink-0" />
                ))}
              <button
                className="prefix-button mono"
                onClick={(e) => openDetail(row.id, e.currentTarget)}
              >
                {row.resource}
              </button>
              <RipeLink value={row.resource} url={row.ripe_url} />
              {row.type === "bgp" && <Badge variant="outline">BGP only</Badge>}
            </div>
          </TableCell>
          <TableCell>
            <OrgLinks handles={row.org_handles} />
          </TableCell>
          <TableCell className="status-cell">
            {row.status || "—"}
            {row.assignment_size && (
              <small className="block muted">
                assignment-size: {row.assignment_size}
              </small>
            )}
          </TableCell>
          <TableCell>
            <Bgp row={row} />
          </TableCell>
          <TableCell className="text-right">
            {row.finding_count ? (
              <Badge variant="destructive">{row.finding_count}</Badge>
            ) : row.descendant_bad ? (
              <span className="muted text-xs">{row.descendant_bad} below</span>
            ) : (
              <span className="muted">—</span>
            )}
          </TableCell>
        </TableRow>
        {isTree && expanded.has(row.id) && children[row.id] && (
          <>
            {resourceRows(children[row.id].items, depth + 1)}
            {children[row.id].items.length < children[row.id].total && (
              <TableRow>
                <TableCell colSpan={5}>
                  <Button
                    variant="ghost"
                    size="sm"
                    disabled={loading.has(row.id)}
                    onClick={() => void loadChildren(row, true)}
                  >
                    Load more children of {row.resource}
                  </Button>
                </TableCell>
              </TableRow>
            )}
          </>
        )}
      </ReactFragment>
    ));
  }
  return (
    <>
      <div id="data-panel" aria-busy={!page && !error} className="data-panel">
        {error ? (
          <div className="table-empty">
            <TriangleAlert />
            <h3>Could not load resources</h3>
            <p>{error}</p>
            <Button
              variant="outline"
              onClick={() => setRetry((value) => value + 1)}
            >
              Retry
            </Button>
          </div>
        ) : !page ? (
          <div className="table-empty" role="status">
            <Loader2 className="animate-spin" />
            <p>
              Loading {filters.view === "findings" ? "findings" : "resources"}…
            </p>
          </div>
        ) : !page.items.length ? (
          <div className="table-empty">
            <Search />
            <h3>
              {filters.view === "findings"
                ? "No findings"
                : "No resources found"}
            </h3>
            <p>
              {filters.q || filters.family || filters.bad
                ? "Try changing your search or filters."
                : filters.view === "findings"
                  ? result.coverage.complete
                    ? "No issues found by the automatic checks."
                    : "No findings recorded. Source coverage is incomplete."
                  : "No resources were discovered for this LIR."}
            </p>
          </div>
        ) : (
          <Table className="data-table">
            <TableHeader>
              <TableRow>
                <TableHead>Resource</TableHead>
                {filters.view === "findings" ? (
                  <>
                    <TableHead>Finding</TableHead>
                    <TableHead>Organisation</TableHead>
                    <TableHead className="text-right">Severity</TableHead>
                  </>
                ) : (
                  <>
                    <TableHead>Organisation</TableHead>
                    <TableHead>RIPE status</TableHead>
                    <TableHead>BGP / activity</TableHead>
                    <TableHead className="text-right">Findings</TableHead>
                  </>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {filters.view === "findings"
                ? (page.items as Finding[]).map((finding) => (
                    <TableRow key={finding.id}>
                      <TableCell>
                        <div className="resource-cell">
                          <button
                            className="prefix-button mono"
                            onClick={(e) =>
                              openDetail(finding.resource_id, e.currentTarget)
                            }
                          >
                            {finding.resource}
                          </button>
                          <RipeLink value={finding.resource} />
                        </div>
                      </TableCell>
                      <TableCell className="finding-message">
                        {finding.message_en || finding.message}
                        {finding.announced_prefix && (
                          <small className="block muted mono">
                            {finding.announced_prefix}{" "}
                            {finding.origins.join(", ")}
                          </small>
                        )}
                      </TableCell>
                      <TableCell>
                        <OrgLinks handles={finding.org_handles} />
                      </TableCell>
                      <TableCell className="text-right">
                        <Badge
                          variant={
                            finding.severity === "error"
                              ? "destructive"
                              : "outline"
                          }
                        >
                          {finding.severity}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))
                : resourceRows(page.items as Resource[])}
            </TableBody>
          </Table>
        )}
      </div>
      <div id="pagination" className="pagination">
        <span className="muted text-xs">
          {page
            ? `${page.total ? (filters.page - 1) * filters.limit + 1 : 0}–${Math.min(filters.page * filters.limit, page.total)} of ${page.total.toLocaleString("en-GB")}${isTree ? " root resources" : ""}`
            : ""}
        </span>
        <div className="flex items-center gap-2">
          <span className="muted text-xs rows-label">Rows per page</span>
          <FilterSelect
            value={String(filters.limit)}
            onValueChange={(value) => change({ limit: Number(value) })}
          >
            <SelectTrigger aria-label="Rows per page" className="w-[72px] h-8">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {[25, 50, 100].map((value) => (
                <SelectItem key={value} value={String(value)}>
                  {value}
                </SelectItem>
              ))}
            </SelectContent>
          </FilterSelect>
          <Button
            variant="outline"
            size="sm"
            aria-label="Previous page"
            disabled={!page || filters.page === 1}
            onClick={() => change({ page: filters.page - 1 })}
          >
            <ChevronLeft /> Previous
          </Button>
          <Button
            variant="outline"
            size="sm"
            aria-label="Next page"
            disabled={!page || filters.page * filters.limit >= page.total}
            onClick={() => change({ page: filters.page + 1 })}
          >
            Next <ChevronRight />
          </Button>
        </div>
      </div>
    </>
  );
}
import { Fragment as ReactFragment } from "react";
function ResourceDetails({ row }: { row: Resource }) {
  return (
    <>
      <div className="flex items-center gap-2">
        <Badge variant="outline">{row.status || row.type}</Badge>
        <RipeLink value={row.resource} url={row.ripe_url} />
      </div>
      <dl className="detail-list">
        <dt>Organisation</dt>
        <dd>
          <OrgLinks handles={row.org_handles} />
          {row.org_names.map((name) => (
            <small className="block muted" key={name}>
              {name}
            </small>
          ))}
        </dd>
        <dt>ORG source</dt>
        <dd>
          {row.org_source || "—"} {row.org_source_id}
        </dd>
        <dt>Type</dt>
        <dd>{row.type}</dd>
        <dt>BGP / activity</dt>
        <dd>
          <Bgp row={row} />
        </dd>
        <dt>Children</dt>
        <dd>{row.children_count}</dd>
        {row.assignment_size && (
          <>
            <dt>Assignment size</dt>
            <dd>/{row.assignment_size}</dd>
          </>
        )}
        {row.last_seen && (
          <>
            <dt>Last announcement</dt>
            <dd>{date(row.last_seen)}</dd>
          </>
        )}
        {row.created && (
          <>
            <dt>Registered</dt>
            <dd>{date(row.created)}</dd>
          </>
        )}
      </dl>
      <h3>Findings ({row.finding_details?.length || 0})</h3>
      {row.finding_details?.length ? (
        row.finding_details.map((finding) => (
          <div className="detail-finding" key={finding.id}>
            <Badge
              variant={finding.severity === "error" ? "destructive" : "outline"}
            >
              {finding.severity}
            </Badge>
            <p>{finding.message_en || finding.message}</p>
            <details>
              <summary>Technical details</summary>
              <dl className="detail-list">
                <dt>Code</dt>
                <dd className="mono">{finding.code}</dd>
                <dt>Basis</dt>
                <dd>{finding.basis.replaceAll("_", " ")}</dd>
                {finding.source_url && (
                  <>
                    <dt>Reference</dt>
                    <dd>
                      <a
                        className="underline"
                        href={finding.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                      >
                        Source <ArrowUpRight className="inline size-3" />
                      </a>
                    </dd>
                  </>
                )}
              </dl>
            </details>
          </div>
        ))
      ) : (
        <p className="muted text-sm">No findings recorded for this resource.</p>
      )}
      <h3>Announcements</h3>
      {row.route_details?.length ? (
        <div className="route-list">
          {row.route_details.map((route, i) => (
            <div className="flex flex-wrap justify-between gap-2 mono" key={i}>
              <span>{route.prefix}</span>
              <span className="muted">{route.origins.join(", ")}</span>
            </div>
          ))}
        </div>
      ) : (
        <p className="muted text-sm">
          No announcements recorded in this snapshot.
        </p>
      )}
    </>
  );
}
function Rules() {
  return (
    <div className="rules">
      <h3>IPv6</h3>
      <ul>
        <li>ASSIGNED prefix lengths must be /48–/64.</li>
        <li>
          Each announcement is checked separately for a contained ASSIGNED or
          valid AGGREGATED-BY-LIR registration. Full coverage is not required.
        </li>
        <li>
          IPv6 aggregate assignment-size must be /48–/64 and fit within the
          aggregate.
        </li>
        <li>No more than five nested ALLOCATED-BY-LIR objects.</li>
      </ul>
      <h3>IPv4</h3>
      <p>
        <a
          className="underline"
          href="https://www.ripe.net/publications/docs/ripe-826/#50-policies-and-guidelines-for-allocations"
          target="_blank"
          rel="noopener noreferrer"
        >
          RIPE-826
        </a>
        : PA/PI status consistency, SUB-ALLOCATED PA parentage and terminal
        assignment nesting. Missing assignments inferred from announcements are
        flagged for review. Existing allocations larger than /24 are not
        automatically violations.
      </p>
      <h3>ASN</h3>
      <p>
        Flag own and sponsored ASNs with no observed IPv4 or IPv6 origin
        announcements for more than three calendar months. Newly registered ASNs
        are excluded until that period has elapsed.
      </p>
      <h3>Manual review</h3>
      <p>
        Contracts, assignment justification, utilisation and IPv4 aggregate
        assignment statistics require separate verification.
      </p>
    </div>
  );
}
