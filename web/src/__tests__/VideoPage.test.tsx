import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { processingDetail } from "./fixtures";
import { renderAt } from "./render";
import { server } from "./server";

test("summary tab shows overview, decisions, discussion and action items", async () => {
  renderAt("/videos/team-meeting");
  expect(await screen.findByRole("tab", { name: /Summary/ })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByRole("tab", { name: /Diary/ })).not.toBeInTheDocument();
});

test("summary tab content", async () => {
  renderAt("/videos/team-meeting");
  expect(await screen.findByRole("heading", { level: 2, name: "Weekly Student Success Meeting" })).toBeInTheDocument();
  expect(screen.getByText(/Staff discussed Friday absences/)).toBeInTheDocument();
  expect(screen.getByText("Key Decisions")).toBeInTheDocument();
  expect(screen.getByText("Try a pancake breakfast next Friday")).toBeInTheDocument();
  expect(screen.getByText("Many students skip on Fridays.")).toBeInTheDocument();
  expect(screen.getByText("Put up flu-season posters")).toBeInTheDocument();
  expect(screen.getByRole("checkbox")).not.toBeChecked();
});

test("key moments and timestamps seek the video", async () => {
  renderAt("/videos/team-meeting");
  await screen.findByText("Key Moments");
  const video = document.querySelector("video")!;
  await userEvent.click(screen.getByRole("button", { name: /John Smith/ }));
  expect(video.currentTime).toBe(60);
  await userEvent.click(screen.getAllByRole("button", { name: "00:17" })[0]);
  expect(video.currentTime).toBe(17);
});

test("transcript, speakers and scenes tabs", async () => {
  renderAt("/videos/team-meeting");
  await screen.findByText("Key Moments");

  await userEvent.click(screen.getByRole("tab", { name: /Transcript/ }));
  expect(screen.getByText("Hello everyone, thanks for coming.")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Search the transcript"), "fridays");
  expect(screen.queryByText("Hello everyone, thanks for coming.")).not.toBeInTheDocument();
  expect(screen.getByText("My mentees find Fridays hard.")).toBeInTheDocument();

  await userEvent.click(screen.getByRole("tab", { name: /Speakers/ }));
  expect(screen.getByText(/2 speakers identified/)).toBeInTheDocument();

  await userEvent.click(screen.getByRole("tab", { name: /Scenes/ }));
  expect(screen.getByAltText("Scene at 00:03")).toBeInTheDocument();
});

test("processing video shows its current step", async () => {
  server.use(http.get("*/api/videos/:id", () => HttpResponse.json(processingDetail)));
  renderAt("/videos/travel-vlog");
  expect(await screen.findByText("Identifying speakers")).toBeInTheDocument();
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "45");
  expect(screen.queryByRole("tab")).not.toBeInTheDocument();
});

test("failed video shows the error", async () => {
  server.use(http.get("*/api/videos/:id", () =>
    HttpResponse.json({ ...processingDetail, status: "failed", step: "failed", error: "HF_TOKEN is not set." })));
  renderAt("/videos/travel-vlog");
  expect(await screen.findByRole("alert")).toHaveTextContent("HF_TOKEN is not set.");
});

test("naming speakers saves and refreshes the page", async () => {
  let saved: Record<string, string> | null = null;
  server.use(http.put("*/api/videos/:id/speakers", async ({ request }) => {
    saved = ((await request.json()) as { names: Record<string, string> }).names;
    return HttpResponse.json({ speakers: [], indexed: true, index_error: null });
  }));
  renderAt("/videos/team-meeting");
  await screen.findByText("Key Moments");
  await userEvent.click(screen.getByRole("tab", { name: /Speakers/ }));
  await userEvent.click(screen.getByRole("button", { name: /Name speakers/ }));
  await userEvent.type(screen.getByLabelText("Name for SPEAKER_00"), "Ms. Lee");
  await userEvent.click(screen.getByRole("button", { name: /Save names/ }));
  expect(await screen.findByRole("status")).toHaveTextContent("Names saved");
  expect(saved).toEqual({ SPEAKER_00: "Ms. Lee", SPEAKER_01: "" });
});

test("opening with ?t= starts the video at that time", async () => {
  renderAt("/videos/team-meeting?t=42");
  await screen.findByText("Key Moments");
  const video = document.querySelector("video")!;
  Object.defineProperty(video, "readyState", { configurable: true, value: 1 });
  video.dispatchEvent(new Event("loadedmetadata"));
  expect(video.currentTime).toBe(42);
});
