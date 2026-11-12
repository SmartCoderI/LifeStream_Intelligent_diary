import { http, HttpResponse } from "msw";
import { setupServer } from "msw/node";
import { day, doneVideo, meetingDetail } from "./fixtures";

export const server = setupServer(
  http.get("*/api/videos", () => HttpResponse.json([doneVideo])),
  http.get("*/api/videos/:id", () => HttpResponse.json(meetingDetail)),
  http.get("*/api/days", () => HttpResponse.json([{ date: "2026-11-04", video_count: 1 }])),
  http.get("*/api/days/:date", () => HttpResponse.json(day)),
);
