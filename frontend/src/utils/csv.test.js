import "@testing-library/jest-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { CandidateGenesTable } from "../components/CandidateGenesTable";
import { PeakComparisonTable } from "../components/PeakComparisonTable";
import { sanitizeCsvCell, toCsv } from "./csv";

describe("CSV injection sanitization and numeric preservation (A16)", () => {
  test("prefixes formula-starting strings while keeping numeric values intact", () => {
    expect(sanitizeCsvCell('=HYPERLINK("x")')).toBe('\'=HYPERLINK("x")');
    expect(sanitizeCsvCell("+cmd")).toBe("'+cmd");
    expect(sanitizeCsvCell("-cmd")).toBe("'-cmd");
    expect(sanitizeCsvCell("@SUM(1)")).toBe("'@SUM(1)");
    expect(sanitizeCsvCell("\tcell")).toBe("'\tcell");
    expect(sanitizeCsvCell("\rcell")).toBe("'\rcell");

    expect(sanitizeCsvCell(-1.5e-8)).toBe(String(-1.5e-8));
    expect(sanitizeCsvCell("-1.5e-8")).toBe("-1.5e-8");
    expect(sanitizeCsvCell(-0.42)).toBe("-0.42");
    expect(sanitizeCsvCell("-0.42")).toBe("-0.42");
  });

  test("toCsv quotes every field and escapes embedded double quotes", () => {
    const csv = toCsv(
      ["SNP", "Beta", "Note"],
      [
        ['rs1', -0.42, 'a"b'],
        ['=HYPERLINK("x")', "-1.5e-8", "-cmd"],
      ]
    );
    const lines = csv.split("\n");
    expect(lines).toHaveLength(3);
    expect(lines[0]).toBe('"SNP","Beta","Note"');
    expect(lines[1]).toBe('"rs1","-0.42","a""b"');
    expect(lines[2]).toBe('"\'=HYPERLINK(""x"")","-1.5e-8","\'-cmd"');
  });

  test("CandidateGenesTable and PeakComparisonTable export CSV using toCsv", () => {
    const capturedBlobs = [];
    const origBlob = globalThis.Blob;
    const origCreateObjectURL = URL.createObjectURL;
    const origRevokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = jest.fn(() => "blob:mock");
    URL.revokeObjectURL = jest.fn();
    globalThis.Blob = function MockBlob(parts, options) {
      capturedBlobs.push(parts.join(""));
      return new origBlob(parts, options);
    };

    try {
      render(
        <CandidateGenesTable
          jobId="job-1"
          annotationData={{
            total_lead_snps: 1,
            mappings: [
              {
                snp: '=HYPERLINK("x")',
                chr: "1",
                pos: 100,
                p: 1e-8,
                category: "Genik",
                nearest_gene: "G1",
                nearest_gene_name: "G1",
                distance_bp: 0,
                strand: "+",
                description: 'a"b',
                genes_in_window: [],
              },
            ],
          }}
        />
      );
      fireEvent.click(screen.getByRole("button", { name: /Unduh Tabel CSV/i }));

      render(
        <PeakComparisonTable
          comparison={{
            consistent: [
              {
                snp: '=CMD("y")',
                chr: "1",
                pos: 100,
                method: "BLINK",
                p_single: 1e-8,
                p_multi: "-1.5e-8",
                effect: -0.42,
                pve_pct: 12.5,
                status: "Konsisten",
                keterangan: 'quoted "note"',
              },
            ],
            single_only: [],
            multi_only: [],
            summary: { n_consistent: 1, n_multi_only: 0, n_single_only: 0 },
          }}
          multilocusSummary={{
            lead_snps: [
              {
                trait: "T",
                method: "BLINK",
                snp: "+EVIL",
                chr: "1",
                pos: 100,
                p: "-1.5e-8",
                maf: 0.2,
                nobs: 50,
                effect: -0.42,
                h_b_p_value: 1e-7,
                pve_pct: 12.5,
              },
            ],
          }}
        />
      );
      fireEvent.click(screen.getByRole("button", { name: /Unduh CSV PVE/i }));
      fireEvent.click(screen.getByRole("button", { name: /Unduh Ringkasan_Puncak_GWAS\.csv/i }));

      expect(capturedBlobs).toHaveLength(3);
      expect(capturedBlobs[0]).toContain('"\'=HYPERLINK(""x"")"');
      expect(capturedBlobs[0]).toContain('"a""b"');
      expect(capturedBlobs[1]).toContain('"\'+EVIL"');
      expect(capturedBlobs[1]).toContain('"-0.42"');
      expect(capturedBlobs[1]).toContain('"-1.5e-8"');
      expect(capturedBlobs[2]).toContain('"\'=CMD(""y"")"');
      expect(capturedBlobs[2]).toContain('"-0.42"');
      expect(capturedBlobs[2]).toContain('"-1.5e-8"');
      expect(capturedBlobs[2]).toContain('"quoted ""note"""');
    } finally {
      globalThis.Blob = origBlob;
      URL.createObjectURL = origCreateObjectURL;
      URL.revokeObjectURL = origRevokeObjectURL;
    }
  });
});
