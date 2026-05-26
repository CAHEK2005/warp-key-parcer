import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { App } from "./App";

describe("App", () => {
  it("renders the operational dashboard with key work areas", () => {
    render(<App />);

    expect(screen.getByRole("heading", { name: "WARP+ Orchestrator" })).toBeInTheDocument();
    expect(screen.getByText("Hosts")).toBeInTheDocument();
    expect(screen.getByText("Telegram sources")).toBeInTheDocument();
    expect(screen.getByText("WARP keys")).toBeInTheDocument();
    expect(screen.getByText("Job stream")).toBeInTheDocument();
  });

  it("keeps secrets masked until the operator reveals them", async () => {
    const user = userEvent.setup();
    render(<App />);

    expect(screen.getByText("•••• •••• •••• 9abc")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /reveal warp key 9abc/i }));

    expect(screen.getByText("12345678-1234-1234-1234-123456789abc")).toBeInTheDocument();
  });
});
