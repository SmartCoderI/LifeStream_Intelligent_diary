import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { renderAt } from "./render";
import { server } from "./server";

test("opens the latest day with each video's badge and summary", async () => {
  renderAt("/diary");
  expect(await screen.findByRole("heading", { name: "Wed, Nov 4, 2026" })).toBeInTheDocument();
  expect(screen.getByText("2 videos recorded")).toBeInTheDocument();
  const days = screen.getByRole("navigation", { name: "Days" });
  expect(within(days).getAllByRole("link")).toHaveLength(2);
});

test("meeting format: topics with speaker and timestamp for each point", async () => {
  renderAt("/diary/2026-11-04");
  const meeting = await screen.findByRole("region", { name: "Weekly Student Success Meeting" });
  expect(within(meeting).getByText("meeting")).toHaveClass("badge");
  expect(within(meeting).getByText("Friday absenteeism")).toBeInTheDocument();
  const point = within(meeting).getByText("Many students skip on Fridays.").closest(".point") as HTMLElement;
  expect(within(point).getByText("SPEAKER_00")).toBeInTheDocument();
  expect(within(point).getByRole("button", { name: "00:05" })).toBeInTheDocument();
  expect(within(meeting).getByText("Try a pancake breakfast next Friday")).toBeInTheDocument();
});

test("vlog format: activities with times, no speakers", async () => {
  renderAt("/diary/2026-11-04");
  const vlog = await screen.findByRole("region", { name: "Morning at the farmers market" });
  expect(within(vlog).getByText("vlog")).toHaveClass("badge");
  expect(within(vlog).getByText("Activity")).toBeInTheDocument();
  expect(within(vlog).getByText("Tasted peaches at the first stall")).toBeInTheDocument();
  expect(within(vlog).getByRole("button", { name: "01:10" })).toBeInTheDocument();
  expect(within(vlog).queryByText("Discussion")).not.toBeInTheDocument();
  expect(within(vlog).getByText(/Berkeley/)).toBeInTheDocument();
});

test("clicking a timestamp opens the video at that moment", async () => {
  renderAt("/diary/2026-11-04");
  const meeting = await screen.findByRole("region", { name: "Weekly Student Success Meeting" });
  await userEvent.click(within(meeting).getByRole("button", { name: "00:17" }));
  await screen.findByText("Key Moments");  // the video page
  const video = document.querySelector("video")!;
  Object.defineProperty(video, "readyState", { configurable: true, value: 1 });
  video.dispatchEvent(new Event("loadedmetadata"));
  expect(video.currentTime).toBe(17);
});

test("loading state", async () => {
  server.use(http.get("*/api/days/:date", async () => { await delay("infinite"); return HttpResponse.json({}); }));
  renderAt("/diary/2026-11-04");
  await screen.findByRole("navigation", { name: "Days" });  // the day list loaded; the day itself is pending
  expect(screen.getByText("Loading…")).toBeInTheDocument();
  expect(screen.queryByText(/videos recorded/)).not.toBeInTheDocument();
});

test("error state", async () => {
  server.use(http.get("*/api/days/:date", () => HttpResponse.json({ detail: "No diary for 2026-11-04" }, { status: 404 })));
  renderAt("/diary/2026-11-04");
  expect(await screen.findByRole("alert")).toHaveTextContent("No diary for 2026-11-04");
});

test("no entries yet", async () => {
  server.use(http.get("*/api/days", () => HttpResponse.json([])));
  renderAt("/diary");
  expect(await screen.findByText(/No diary entries yet/)).toBeInTheDocument();
});

test("sidebar links to the diary", () => {
  renderAt("/videos");
  expect(screen.getByRole("link", { name: /Diary/ })).toHaveAttribute("href", "/diary");
});
