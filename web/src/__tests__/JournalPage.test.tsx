import { screen } from "@testing-library/react";
import { http, HttpResponse } from "msw";
import { renderAt } from "./render";
import { server } from "./server";

test("opens the latest day and lists its videos and action items", async () => {
  renderAt("/journal");
  expect(await screen.findByRole("heading", { name: /Weekly Student Success Meeting/ })).toBeInTheDocument();
  expect(screen.getByText("1 video recorded")).toBeInTheDocument();
  expect(screen.getByText(/Staff discussed Friday absences/)).toBeInTheDocument();
  expect(screen.getByText(/Put up flu-season posters/)).toBeInTheDocument();
});

test("no journal entries yet", async () => {
  server.use(http.get("*/api/days", () => HttpResponse.json([])));
  renderAt("/journal");
  expect(await screen.findByText(/No journal entries yet/)).toBeInTheDocument();
});

test("old /diaries links still work", async () => {
  renderAt("/diaries/2026-11-04");
  expect(await screen.findByRole("heading", { name: "Daily Journal" })).toBeInTheDocument();
  expect(await screen.findByText("1 video recorded")).toBeInTheDocument();
});

test("sidebar shows the new names", async () => {
  renderAt("/journal");
  expect(screen.getByRole("link", { name: /Daily Journal/ })).toHaveAttribute("href", "/journal");
  expect(screen.queryByText("Diaries")).not.toBeInTheDocument();
});
