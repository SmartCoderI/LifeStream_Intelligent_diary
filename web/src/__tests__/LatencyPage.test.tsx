import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { cloudRun, cloudSummary, cloudSummaryWithBudget, processingVideo, runs } from "./fixtures";
import { renderAt } from "./render";
import { server } from "./server";

test("no numbers pooled across runs: no medians, shares or run counts", async () => {
  renderAt("/latency");
  await screen.findByText("Runs");
  expect(document.querySelector(".kpi")).toBeNull();
  expect(screen.queryByText("Where the time goes")).not.toBeInTheDocument();
  expect(screen.queryByText(/full runs?/)).not.toBeInTheDocument();
  expect(screen.queryByText(/left out/)).not.toBeInTheDocument();
  expect(screen.queryByText("2.35×")).not.toBeInTheDocument();          // median speed
  expect(screen.queryByText("Time budget (1-hour video)")).not.toBeInTheDocument();  // cloud only
});

test("the selected run's breakdown: ranked kinds of work with share", async () => {
  renderAt("/latency");
  await screen.findByText("Runs");
  const table = document.querySelector<HTMLElement>(".detail .bd-table")!;
  const rows = within(table).getAllByRole("row");
  expect(rows[0]).toHaveTextContent("Transcription (WhisperX)");
  expect(rows[0]).toHaveTextContent("42%");
  expect(rows[1]).toHaveTextContent("LLM (Claude)");
  expect(screen.getByText(/except scene detection, which runs/)).toBeInTheDocument();
});

test("runs table is concise and a run's steps are behind a toggle", async () => {
  renderAt("/latency");
  await screen.findByText("Runs");
  const table = document.querySelectorAll<HTMLElement>("table.latency-table")[0];
  expect(within(table).getAllByRole("columnheader").map((h) => h.textContent))
    .toEqual(["When", "Video", "Length", "Total", "Speed", "Bottleneck"]);
  const first = within(table).getAllByRole("row")[1];
  expect(first).toHaveTextContent("Weekly Student Success Meeting");
  expect(first).toHaveTextContent("2 min 00 s");
  expect(first).toHaveTextContent("Transcription (WhisperX) · 42%");
  expect(first).toHaveTextContent("reused");

  expect(screen.queryByText("Upload (browser to server)")).not.toBeInTheDocument();
  await userEvent.click(screen.getAllByRole("button", { name: /Show step timeline/ })[0]);
  expect(screen.getByText("Upload (browser to server)")).toBeInTheDocument();
  expect(screen.getByText("4.2k in / 80 out tokens")).toBeInTheDocument();

  await userEvent.click(within(table).getByText("Old upload"));
  expect(screen.getByText("Failed: model crashed")).toBeInTheDocument();
});

test("query table splits LLM, database and embedding time", async () => {
  renderAt("/latency");
  await screen.findByText("Runs");
  const table = document.querySelectorAll<HTMLElement>("table.latency-table")[1];
  const row = within(table).getAllByRole("row")[1];
  expect(row).toHaveTextContent("frontend last week");
  expect(row).toHaveTextContent("3.40 s");   // total
  expect(row).toHaveTextContent("3.20 s");   // LLM
  expect(row).toHaveTextContent("40 ms");    // database
  expect(row).toHaveTextContent("150 ms");   // embedding
});

test("videos processing now are linked", async () => {
  server.use(http.get("*/api/videos", () => HttpResponse.json([processingVideo])));
  renderAt("/latency");
  expect(await screen.findByRole("link", { name: /travel_vlog · Identifying speakers/ }))
    .toHaveAttribute("href", "/videos/travel-vlog");
});

test("empty state", async () => {
  server.use(
    http.get("*/api/latency/runs", () => HttpResponse.json([])),
    http.get("*/api/latency/queries", () => HttpResponse.json([])),
    http.get("*/api/latency/summary", () => HttpResponse.json({
      runs: 0, runs_skipped: 0, config: null, speed_median: null, total_median: null, breakdown: [],
      bottleneck: null, llm_share: 0,
      queries: { count: 0, p50: null, p95: null, llm_p50: null, database_p50: null, embedding_p50: null, breakdown: [] },
    })),
  );
  renderAt("/latency");
  expect(await screen.findByText(/No runs yet/)).toBeInTheDocument();
  expect(screen.getByText("No questions asked yet.")).toBeInTheDocument();
});

test("hovering a breakdown segment explains the category", async () => {
  renderAt("/latency");
  await screen.findByText("Runs");
  const seg = document.querySelector<HTMLElement>(".detail .bd-seg.cat-llm")!;
  await userEvent.hover(seg);
  expect(screen.getByRole("tooltip")).toHaveTextContent("LLM (Claude) · 25%");
  expect(screen.getByRole("tooltip")).toHaveTextContent("classifying the video, writing the summary");
});

// ---------- cloud runs (Stage 3) ----------

test("cloud runs: each run's total and cold start, cold/warm per run", async () => {
  server.use(
    http.get("*/api/latency/summary", () => HttpResponse.json(cloudSummary)),
    http.get("*/api/latency/runs", () => HttpResponse.json([cloudRun, ...runs])),
  );
  renderAt("/latency");
  // only cloud runs are listed, with their cold start
  await screen.findByText("Runs");
  const table = screen.getAllByRole("table").find((t) => within(t).queryByText("Databricks interview"))!;
  expect(within(table).getByRole("columnheader", { name: "Cold start" })).toBeInTheDocument();
  const row = within(table).getByText("Databricks interview").closest("tr")!;
  expect(row).toHaveTextContent("17 min");
  expect(row).toHaveTextContent("7 min 32 s");
  expect(document.querySelector(".kpi")).toBeNull();
  expect(within(table).queryByText("Weekly Student Success Meeting")).not.toBeInTheDocument();
  expect(within(table).getByText("cold")).toBeInTheDocument();
});

test("time budget: targets for a 1-hour video, no medians, and no check until a 1-hour run exists", async () => {
  server.use(http.get("*/api/latency/summary", () => HttpResponse.json(cloudSummary)),
             http.get("*/api/latency/runs", () => HttpResponse.json([cloudRun])));
  renderAt("/latency");
  const budget = (await screen.findByText("Time budget (1-hour video)")).closest(".budget") as HTMLElement;
  const row = within(budget).getByText("WhisperX large-v3").closest("tr")!;
  expect(row).toHaveTextContent("4–8 min");
  expect(within(budget).getAllByRole("columnheader").map((h) => h.textContent))
    .toEqual(["Budget line", "Target", "Latest 1-hour run"]);
  expect(within(budget).getByText(/No 1-hour video processed in the cloud yet/)).toBeInTheDocument();
});

test("time budget: the latest 1-hour run passes overall but its cold start is over", async () => {
  server.use(http.get("*/api/latency/summary", () => HttpResponse.json(cloudSummaryWithBudget)),
             http.get("*/api/latency/runs", () => HttpResponse.json([cloudRun])));
  renderAt("/latency");
  const budget = (await screen.findByText("Time budget (1-hour video)")).closest(".budget") as HTMLElement;
  const cold = within(budget).getByText("Cold start (instance + image)").closest("tr")!;
  expect(within(cold).getByText(/over/)).toHaveClass("over");
  const total = within(budget).getByText("Upload → done").closest("tr")!;
  expect(total).toHaveTextContent("< 20 min");
  expect(within(total).getByText(/pass/)).toHaveClass("ok");
});

test("switching between cloud and this computer", async () => {
  const asked: (string | null)[] = [];
  server.use(
    http.get("*/api/latency/summary", ({ request }) => {
      const env = new URL(request.url).searchParams.get("environment");
      asked.push(env);
      return HttpResponse.json(env === "local"
        ? { ...cloudSummary, environment: "local", cold_start_median: null, budget_lines: null } : cloudSummary);
    }),
    http.get("*/api/latency/runs", () => HttpResponse.json([cloudRun, ...runs])),
  );
  renderAt("/latency");
  const local = await screen.findByRole("button", { name: "This computer (3)" });
  expect(screen.getByRole("button", { name: "Cloud GPU (2)" })).toHaveAttribute("aria-pressed", "true");
  await userEvent.click(local);
  await waitFor(() => expect(screen.queryByText("Time budget (1-hour video)")).not.toBeInTheDocument());
  expect(asked).toContain("local");
  const table = screen.getAllByRole("table").find((t) => within(t).queryByText("Weekly Student Success Meeting"));
  expect(table).toBeDefined();
});

test("cloud: LLM and database time stay visible, and parallel scene detection is explained", async () => {
  server.use(http.get("*/api/latency/summary", () => HttpResponse.json(cloudSummary)),
             http.get("*/api/latency/runs", () => HttpResponse.json([cloudRun])));
  renderAt("/latency");
  await screen.findByText("Runs");
  const table = document.querySelector<HTMLElement>(".detail .bd-table")!;
  expect(within(table).getByText("LLM (Claude)")).toBeInTheDocument();
  expect(within(table).getByText("Database")).toBeInTheDocument();
  expect(screen.getByText(/Scene detection \+ keyframes \(parallel\) ran alongside other steps for 1 min 00 s; all of it was hidden/))
    .toBeInTheDocument();
});
