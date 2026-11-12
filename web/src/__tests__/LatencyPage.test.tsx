import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { processingVideo } from "./fixtures";
import { renderAt } from "./render";
import { server } from "./server";

test("headline numbers: speed, bottleneck, Claude share, query latency", async () => {
  renderAt("/latency");
  expect(await screen.findByText("2.35×")).toBeInTheDocument();
  const bottleneck = screen.getByText("Bottleneck", { selector: ".kpi-label" }).parentElement!;
  expect(bottleneck).toHaveTextContent("Speech models");
  expect(bottleneck).toHaveTextContent("55% of processing time");
  expect(screen.getByText("Claude share", { selector: ".kpi-label" }).parentElement).toHaveTextContent("20%");
  expect(screen.getByText("Ask / Search", { selector: ".kpi-label" }).parentElement)
    .toHaveTextContent("p50 · p95 4.10 s · 12 queries");
  expect(screen.getByText(/Last 3 full runs · CPU · medium.en int8 · claude-opus-5-5/)).toBeInTheDocument();
  expect(screen.getByText(/1 run left out/)).toBeInTheDocument();
});

test("where the time goes: ranked categories with share and time per video minute", async () => {
  renderAt("/latency");
  await screen.findByText("Where the time goes");
  const table = document.querySelector<HTMLElement>(".overview .bd-table")!;
  const rows = within(table).getAllByRole("row");
  expect(rows[0]).toHaveTextContent("Speech models");
  expect(rows[0]).toHaveTextContent("55%");
  expect(rows[0]).toHaveTextContent("1 min 16 s / video min");
  expect(rows[1]).toHaveTextContent("Claude");
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
  expect(first).toHaveTextContent("Speech models · 42%");
  expect(first).toHaveTextContent("reused");

  expect(screen.queryByText("Upload (browser to server)")).not.toBeInTheDocument();
  await userEvent.click(screen.getAllByRole("button", { name: /Show step timeline/ })[0]);
  expect(screen.getByText("Upload (browser to server)")).toBeInTheDocument();
  expect(screen.getByText("4.2k in / 80 out tokens")).toBeInTheDocument();

  await userEvent.click(within(table).getByText("Old upload"));
  expect(screen.getByText("Failed: model crashed")).toBeInTheDocument();
});

test("query table splits Claude, database and embedding time", async () => {
  renderAt("/latency");
  await screen.findByText("Runs");
  const table = document.querySelectorAll<HTMLElement>("table.latency-table")[1];
  const row = within(table).getAllByRole("row")[1];
  expect(row).toHaveTextContent("frontend last week");
  expect(row).toHaveTextContent("3.40 s");   // total
  expect(row).toHaveTextContent("3.20 s");   // Claude
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
  expect(screen.getByText("No full runs yet")).toBeInTheDocument();
  expect(screen.getByText("No questions asked yet.")).toBeInTheDocument();
});

test("hovering a breakdown segment explains the category", async () => {
  renderAt("/latency");
  await screen.findByText("Where the time goes");
  const seg = document.querySelector<HTMLElement>(".overview .bd-seg.cat-llm")!;
  await userEvent.hover(seg);
  expect(screen.getByRole("tooltip")).toHaveTextContent("Claude · 20%");
  expect(screen.getByRole("tooltip")).toHaveTextContent("Claude calls");
});
