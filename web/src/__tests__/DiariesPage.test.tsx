import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { renderAt } from "./render";
import { server } from "./server";

test("opens the latest day and lists its videos and action items", async () => {
  renderAt("/diaries");
  expect(await screen.findByRole("heading", { name: /Weekly Student Success Meeting/ })).toBeInTheDocument();
  expect(screen.getByText("1 video recorded")).toBeInTheDocument();
  expect(screen.getByText(/Staff discussed Friday absences/)).toBeInTheDocument();
  expect(screen.getByText(/Put up flu-season posters/)).toBeInTheDocument();
});

test("no diaries yet", async () => {
  server.use(http.get("*/api/days", () => HttpResponse.json([])));
  renderAt("/diaries");
  expect(await screen.findByText(/No diaries yet/)).toBeInTheDocument();
});
