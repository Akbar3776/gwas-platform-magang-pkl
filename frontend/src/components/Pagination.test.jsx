import "@testing-library/jest-dom";
import { render, screen, fireEvent } from "@testing-library/react";
import React from "react";
import { Pagination } from "./Pagination";

describe("Pagination Component", () => {
  test("renders nothing when totalItems is 0", () => {
    const { container } = render(
      <Pagination
        currentPage={1}
        totalItems={0}
        pageSize={10}
        onPageChange={jest.fn()}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  test("renders range summary correctly", () => {
    render(
      <Pagination
        currentPage={2}
        totalItems={45}
        pageSize={10}
        onPageChange={jest.fn()}
        itemLabel="Lead SNPs"
      />
    );

    // Should show 11–20 from 45 Lead SNPs
    expect(screen.getByText(/Lead SNPs/)).toBeInTheDocument();
    expect(screen.getByText("11")).toBeInTheDocument();
    expect(screen.getByText("20")).toBeInTheDocument();
    expect(screen.getByText("45")).toBeInTheDocument();
  });

  test("navigates forward and backward when arrow buttons clicked", () => {
    const handlePageChange = jest.fn();
    render(
      <Pagination
        currentPage={2}
        totalItems={50}
        pageSize={10}
        onPageChange={handlePageChange}
      />
    );

    const prevBtn = screen.getByTitle("Halaman Sebelumnya");
    const nextBtn = screen.getByTitle("Halaman Berikutnya");

    fireEvent.click(prevBtn);
    expect(handlePageChange).toHaveBeenCalledWith(1);

    fireEvent.click(nextBtn);
    expect(handlePageChange).toHaveBeenCalledWith(3);
  });

  test("disables prev and first buttons on page 1", () => {
    render(
      <Pagination
        currentPage={1}
        totalItems={30}
        pageSize={10}
        onPageChange={jest.fn()}
      />
    );

    expect(screen.getByTitle("Halaman Sebelumnya")).toBeDisabled();
    expect(screen.getByTitle("Halaman Pertama")).toBeDisabled();
    expect(screen.getByTitle("Halaman Berikutnya")).not.toBeDisabled();
    expect(screen.getByTitle("Halaman Terakhir")).not.toBeDisabled();
  });

  test("disables next and last buttons on last page", () => {
    render(
      <Pagination
        currentPage={3}
        totalItems={30}
        pageSize={10}
        onPageChange={jest.fn()}
      />
    );

    expect(screen.getByTitle("Halaman Sebelumnya")).not.toBeDisabled();
    expect(screen.getByTitle("Halaman Pertama")).not.toBeDisabled();
    expect(screen.getByTitle("Halaman Berikutnya")).toBeDisabled();
    expect(screen.getByTitle("Halaman Terakhir")).toBeDisabled();
  });

  test("triggers onPageSizeChange and resets page to 1 when page size changed", () => {
    const handlePageSizeChange = jest.fn();
    const handlePageChange = jest.fn();

    render(
      <Pagination
        currentPage={3}
        totalItems={100}
        pageSize={10}
        onPageChange={handlePageChange}
        onPageSizeChange={handlePageSizeChange}
        pageSizeOptions={[10, 25, 50]}
      />
    );

    const select = screen.getByRole("combobox");
    fireEvent.change(select, { target: { value: "25" } });

    expect(handlePageSizeChange).toHaveBeenCalledWith(25);
    expect(handlePageChange).toHaveBeenCalledWith(1);
  });
});
