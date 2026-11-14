/** Cancelling a video while it uploads, waits or processes. */
import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { doneVideo, processingDetail, processingVideo } from "./fixtures";
import { renderAt } from "./render";
import { server } from "./server";

afterEach(() => vi.restoreAllMocks());
afterEach(() => vi.unstubAllGlobals());

function rowOf(title: string) {
  return screen.getAllByTestId("video-row").find((r) => within(r).queryByText(title))!;
}

test("a processing video can be cancelled from the list", async () => {
  const cancelled: string[] = [];
  server.use(
    http.get("*/api/videos", () => HttpResponse.json([processingVideo, doneVideo])),
    http.post("*/api/videos/:id/cancel", ({ params }) => {
      cancelled.push(params.id as string);
      return HttpResponse.json({ ...processingVideo, status: "cancelled", step: "cancelled" }, { status: 202 });
    }),
  );
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderAt("/videos");
  const row = await screen.findByText("travel_vlog").then(() => rowOf("travel_vlog"));
  await userEvent.click(within(row).getByRole("button", { name: "Cancel travel_vlog" }));
  expect(window.confirm).toHaveBeenCalled();
  await vi.waitFor(() => expect(cancelled).toEqual(["travel-vlog"]));
  // finished videos have no Cancel button
  expect(within(rowOf("Weekly Student Success Meeting")).queryByRole("button", { name: /Cancel/ })).not.toBeInTheDocument();
});

test("saying no to the confirmation keeps it running", async () => {
  let called = false;
  server.use(
    http.get("*/api/videos", () => HttpResponse.json([processingVideo])),
    http.post("*/api/videos/:id/cancel", () => { called = true; return HttpResponse.json({}); }),
  );
  vi.spyOn(window, "confirm").mockReturnValue(false);
  renderAt("/videos");
  await userEvent.click(await screen.findByRole("button", { name: "Cancel travel_vlog" }));
  expect(called).toBe(false);
});

test("cancelled videos show a badge and can be retried; the last step can't be cancelled", async () => {
  const cancelledVideo = { ...processingVideo, video_id: "c", title: "Cancelled one", status: "cancelled" as const,
                           step: "cancelled", step_label: "Cancelled" };
  const indexing = { ...processingVideo, video_id: "i", title: "Almost done", step: "indexing",
                     step_label: "Adding to search", progress: 96 };
  server.use(http.get("*/api/videos", () => HttpResponse.json([cancelledVideo, indexing])));
  renderAt("/videos");
  const row = await screen.findByText("Cancelled one").then(() => rowOf("Cancelled one"));
  expect(within(row).getByText("Cancelled")).toHaveClass("badge-cancelled");
  expect(within(row).getByRole("button", { name: /Retry/ })).toBeInTheDocument();
  expect(within(row).getByRole("button", { name: "Delete Cancelled one" })).toBeInTheDocument();
  expect(within(rowOf("Almost done")).queryByRole("button", { name: /Cancel/ })).not.toBeInTheDocument();
});

test("the video page has a Cancel button while processing", async () => {
  let cancelled = false;
  server.use(
    http.get("*/api/videos/:id", () => HttpResponse.json(
      cancelled ? { ...processingDetail, status: "cancelled", step: "cancelled", step_label: "Cancelled" } : processingDetail)),
    http.post("*/api/videos/:id/cancel", () => { cancelled = true; return HttpResponse.json({}, { status: 202 }); }),
    http.get("*/api/videos/:id/timeline", () => HttpResponse.json({ video_id: "x", status: "running", spans: [] })),
  );
  vi.spyOn(window, "confirm").mockReturnValue(true);
  renderAt("/videos/travel-vlog");
  await userEvent.click(await screen.findByRole("button", { name: "Cancel" }));
  expect(await screen.findByText(/Cancelled. Retry or delete it/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Cancel" })).not.toBeInTheDocument();
});

test("cancelling an upload stops the transfer and marks the video cancelled (cloud)", async () => {
  const S3 = "https://bucket.s3.amazonaws.com/uploads/trip/trip.mp4?X-Amz-Signature=x";
  const calls: string[] = [];
  server.use(
    http.get("*/api/config", () => HttpResponse.json({ mode: "cloud", auth: false })),
    http.post("*/api/videos", () => HttpResponse.json(
      { ...processingVideo, video_id: "trip", status: "queued", step: "uploading", presigned_put_url: S3,
        upload_headers: { "x-amz-meta-date": "2026-11-13" } }, { status: 201 })),
    http.post("*/api/videos/trip/cancel", () => { calls.push("cancel"); return HttpResponse.json({}, { status: 202 }); }),
  );
  const aborted: string[] = [];
  class NeverFinishes {  // an upload that's still sending until it's aborted
    upload = { onprogress: null as null | ((e: object) => void) };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    onabort: (() => void) | null = null;
    open(_m: string, url: string) { aborted.push(`open ${url}`); }
    setRequestHeader() {}
    send() { this.upload.onprogress?.({ lengthComputable: true, loaded: 30, total: 100 }); }
    abort() { aborted.push("abort"); this.onabort?.(); }
  }
  vi.stubGlobal("XMLHttpRequest", NeverFinishes);

  renderAt("/videos");
  await screen.findByText(/up to 5 GB/);
  await userEvent.upload(screen.getByLabelText("Video file"), new File(["data"], "trip.mp4", { type: "video/mp4" }));
  await userEvent.click(screen.getByRole("button", { name: "Upload and process" }));
  await userEvent.click(await screen.findByRole("button", { name: "Cancel upload" }));
  await vi.waitFor(() => expect(calls).toEqual(["cancel"]));
  expect(aborted).toEqual([`open ${S3}`, "abort"]);
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();  // a cancel isn't an error
  expect(await screen.findByRole("button", { name: "Upload and process" })).toBeInTheDocument();
});
