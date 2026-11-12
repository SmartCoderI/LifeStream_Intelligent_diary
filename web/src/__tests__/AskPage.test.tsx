import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { http, HttpResponse } from "msw";
import { renderAt } from "./render";
import { server } from "./server";

test("asks a question and shows the answer, date range and sources", async () => {
  let sent = "";
  server.use(http.post("*/api/query", async ({ request }) => {
    sent = ((await request.json()) as { question: string }).question;
    return HttpResponse.json((await import("./fixtures")).answer);
  }));
  renderAt("/ask");
  await userEvent.type(screen.getByLabelText("Question"), "What did we discuss about the frontend last week?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));

  expect(await screen.findByText(/hard-coded logo width/)).toBeInTheDocument();
  expect(sent).toBe("What did we discuss about the frontend last week?");
  expect(screen.getByText(/Searched/)).toHaveTextContent("2026");
  expect(screen.getByText("Sources (1)")).toBeInTheDocument();
  const source = screen.getByRole("link", { name: /Frontend Weekly Sync/ });
  expect(source).toHaveAttribute("href", "/videos/team-meeting?t=6");
  expect(source).toHaveTextContent("00:06 – 00:24");
  expect(screen.getByText(/Answered in 3\.40 s/)).toHaveTextContent("search 200 ms, answer 3.20 s");
});

test("example questions can be clicked", async () => {
  renderAt("/ask");
  await userEvent.click(screen.getByRole("button", { name: /Why is John Smith missing school/ }));
  expect(await screen.findByText("Sources (1)")).toBeInTheDocument();
});

test("not-found answers show no sources", async () => {
  server.use(http.post("*/api/query", () => HttpResponse.json({
    question: "budget?", answer: "None of your videos mention a budget.", found: false, citations: [],
    date_from: null, date_to: null, time_phrase: null,
  })));
  renderAt("/ask");
  await userEvent.type(screen.getByLabelText("Question"), "budget?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByText(/mention a budget/)).toBeInTheDocument();
  expect(screen.queryByText(/Sources/)).not.toBeInTheDocument();
});

test("search database not running", async () => {
  server.use(
    http.get("*/api/search/status", () => HttpResponse.json(
      { available: false, videos: 0, chunks: 0, error: "Start it with `docker compose up -d db`." })),
    http.post("*/api/query", () => HttpResponse.json({ detail: "Can't reach the search database." }, { status: 503 })),
  );
  renderAt("/ask");
  expect(await screen.findByText(/Search isn't available/)).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Question"), "anything");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByText("Can't reach the search database.")).toBeInTheDocument();
});
