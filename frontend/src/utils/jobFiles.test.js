import { selectJobFileByName } from "./jobFiles";

describe("selectJobFileByName", () => {
  const sampleFiles = [
    { file_key: "job:qc/keep_ids.txt", filename: "keep_ids.txt", stage: "qc" },
    { file_key: "ds_inspect:keep_ids.txt", filename: "keep_ids.txt", stage: "inspection" },
    { file_key: "job:postgwas/lead_snps.tsv", filename: "lead_snps.tsv", stage: "postgwas" },
  ];

  it("prefers matching stage and preserves exact file_key", () => {
    const keepMatch = selectJobFileByName(sampleFiles, "keep_ids.txt", "inspection");
    expect(keepMatch?.file_key).toBe("ds_inspect:keep_ids.txt");

    const leadMatch = selectJobFileByName(sampleFiles, "lead_snps.tsv", "postgwas");
    expect(leadMatch?.file_key).toBe("job:postgwas/lead_snps.tsv");
  });

  it("falls back across stages or returns null when absent", () => {
    expect(selectJobFileByName(sampleFiles, "lead_snps.tsv")?.file_key).toBe(
      "job:postgwas/lead_snps.tsv"
    );
    expect(selectJobFileByName(sampleFiles, "missing.txt", "inspection")).toBeNull();
  });
});
