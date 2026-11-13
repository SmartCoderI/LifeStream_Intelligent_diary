import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { answer, day, days, doneTimeline, doneVideo, latencySummary, meetingDetail, queries, runs } from "./fixtures";

export const server = setupServer(
  http.get("*/api/config", () => HttpResponse.json({ mode: "local", auth: false })),
  http.get("*/api/days", () => HttpResponse.json(days)),
  http.get("*/api/days/:date", () => HttpResponse.json(day)),
  http.get("*/api/videos", () => HttpResponse.json([doneVideo])),
  http.get("*/api/videos/:id", () => HttpResponse.json(meetingDetail)),
  http.get("*/api/search/status", () => HttpResponse.json({ available: true, videos: 3, chunks: 22, error: null })),
  http.post("*/api/query", () => HttpResponse.json(answer)),
  http.get("*/api/videos/:id/timeline", () => HttpResponse.json(doneTimeline)),
  http.get("*/api/latency/runs", () => HttpResponse.json(runs)),
  http.get("*/api/latency/queries", () => HttpResponse.json(queries)),
  http.get("*/api/latency/summary", () => HttpResponse.json(latencySummary)),
);
