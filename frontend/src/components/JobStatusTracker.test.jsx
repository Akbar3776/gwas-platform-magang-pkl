import "@testing-library/jest-dom";
import { render, screen } from "@testing-library/react";
import { useJobPolling } from "../hooks/useJobPolling";
import { JobStatusTracker } from "./JobStatusTracker";

jest.mock("../hooks/useJobPolling", () => ({
  useJobPolling: jest.fn(),
}));

test("renders the active QC stage without calling the API", () => {
  useJobPolling.mockReturnValue({
    jobStatus: { current_step: "qc", overall_status: "running_qc" },
    status: "RUNNING_QC",
    isPolling: true,
    pollError: null,
  });

  render(<JobStatusTracker jobId="job-1" />);

  expect(screen.getByText("Quality Control sedang berjalan")).toBeInTheDocument();
  expect(screen.getByText("Quality Control")).toBeInTheDocument();
});

test("renders a friendly failed state and server error", () => {
  useJobPolling.mockReturnValue({
    jobStatus: {
      current_step: "qc",
      overall_status: "failed",
      error_message: "Format VCF tidak valid atau rusak.",
    },
    status: "FAILED",
    isPolling: false,
    pollError: null,
  });

  render(<JobStatusTracker jobId="job-2" />);

  expect(screen.getByText("Analisis berhenti karena masalah")).toBeInTheDocument();
  expect(screen.getByText("Format VCF tidak valid atau rusak.")).toBeInTheDocument();
});
