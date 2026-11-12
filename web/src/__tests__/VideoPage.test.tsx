import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { processingDetail } from "./fixtures";
import { renderAt } from "./render";
import { server } from "./server";

test("diary tab shows summary, decisions, discussion and action items", async () => {
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
