import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { doneVideo, failedVideo, processingVideo } from "./fixtures";
import { renderAt } from "./render";
import { server } from "./server";

test("shows processing, completed and failed videos", async () => {
  server.use(http.get("*/api/videos", () => HttpResponse.json([processingVideo, doneVideo, failedVideo])));
  renderAt("/videos");
  const rows = await screen.findAllByTestId("video-row");
  expect(rows).toHaveLength(3);

  expect(within(rows[0]).getByText("Processing…")).toBeInTheDocument();
  expect(within(rows[0]).getByText("45%")).toBeInTheDocument();
  expect(within(rows[0]).getByText("Identifying speakers")).toBeInTheDocument();
  expect(within(rows[0]).getByText(/532 MB/)).toBeInTheDocument();

  expect(within(rows[1]).getByText("Completed")).toBeInTheDocument();
  expect(within(rows[1]).getByRole("link", { name: /View Summary/ })).toHaveAttribute("href", "/videos/team-meeting");
  expect(within(rows[1]).getByText("01:38")).toBeInTheDocument();

  expect(within(rows[2]).getByText("Failed")).toBeInTheDocument();
  expect(within(rows[2]).getByText(/no audio stream/)).toBeInTheDocument();
  expect(within(rows[2]).getByRole("button", { name: /Retry/ })).toBeInTheDocument();
  expect(within(rows[1]).queryByRole("button", { name: /Retry/ })).not.toBeInTheDocument();
});

test("retry re-queues a failed video", async () => {
  let retried = "";
  server.use(
    http.get("*/api/videos", () => HttpResponse.json([failedVideo])),
    http.post("*/api/videos/:id/retry", ({ params }) => {
      retried = String(params.id);
      return HttpResponse.json(failedVideo, { status: 202 });
    }),
  );
  renderAt("/videos");
  await userEvent.click(await screen.findByRole("button", { name: /Retry/ }));
  expect(retried).toBe("broken");
});

test("empty library", async () => {
  server.use(http.get("*/api/videos", () => HttpResponse.json([])));
  renderAt("/videos");
  expect(await screen.findByText(/No videos yet/)).toBeInTheDocument();
});

test("server unreachable", async () => {
  server.use(http.get("*/api/videos", () => HttpResponse.json({ detail: "boom" }, { status: 500 })));
  renderAt("/videos");
  expect(await screen.findByRole("alert")).toHaveTextContent("boom");
});

test("rejects unsupported file types before uploading", async () => {
  renderAt("/videos");
  const input = screen.getByLabelText("Video file");
  await userEvent.upload(input, new File(["x"], "notes.txt", { type: "text/plain" }), { applyAccept: false });
  expect(screen.getByRole("alert")).toHaveTextContent("not a supported video type");
  expect(screen.getByRole("button", { name: "Upload and process" })).toBeDisabled();
});

/** MSW can't complete jsdom XHR uploads with a FormData body, so upload tests use a fake XHR. */
function fakeXhr(status: number, body: object) {
  const sent: { url: string; form: FormData | null } = { url: "", form: null };
  class FakeXHR {
    status = 0;
    responseText = "";
    upload: { onprogress: ((e: { lengthComputable: boolean; loaded: number; total: number }) => void) | null } = { onprogress: null };
    onload: (() => void) | null = null;
    onerror: (() => void) | null = null;
    open(_method: string, url: string) { sent.url = url; }
    send(form: FormData) {
      sent.form = form;
      setTimeout(() => {
        this.upload.onprogress?.({ lengthComputable: true, loaded: 50, total: 100 });
        this.status = status;
        this.responseText = JSON.stringify(body);
        this.onload?.();
      }, 10);
    }
  }
  vi.stubGlobal("XMLHttpRequest", FakeXHR);
  return sent;
}

afterEach(() => vi.unstubAllGlobals());

test("uploads a video with its date", async () => {
  const sent = fakeXhr(202, { ...processingVideo, status: "queued" });
  renderAt("/videos");
  await userEvent.upload(screen.getByLabelText("Video file"), new File(["data"], "trip.mp4", { type: "video/mp4" }));
  expect(screen.getByText("trip.mp4")).toBeInTheDocument();
  const date = screen.getByLabelText("Recording date");
  await userEvent.clear(date);
  await userEvent.type(date, "2026-11-01");
  await userEvent.click(screen.getByRole("button", { name: "Upload and process" }));
  await screen.findByRole("button", { name: "Upload and process" });
  expect(sent.url).toBe("/api/videos");
  expect(sent.form!.get("date")).toBe("2026-11-01");
  expect((sent.form!.get("file") as File).name).toBe("trip.mp4");
  expect(Number(sent.form!.get("client_started_at"))).toBeGreaterThan(Date.now() - 60_000);
  expect(screen.queryByText("trip.mp4")).not.toBeInTheDocument();
});

test("upload error is shown", async () => {
  fakeXhr(413, { detail: "File is larger than 1024 MB" });
  renderAt("/videos");
  await userEvent.upload(screen.getByLabelText("Video file"), new File(["data"], "big.mp4", { type: "video/mp4" }));
  await userEvent.click(screen.getByRole("button", { name: "Upload and process" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("larger than 1024 MB");
});

test("old journal links go to the diary", async () => {
  renderAt("/journal/2026-11-04");
  expect(await screen.findByText("2 videos recorded")).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "Diary" })).toBeInTheDocument();
});
