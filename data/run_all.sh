#!/bin/bash
{
set -euo pipefail

text_highlight() { tput setab 3; tput setaf 0; }
text_default() { tput sgr0; }
run() {
    text_highlight; echo; echo "=> Starting $1"; text_default
    start_time=$(date +%s)
    set +e
    $1
    exit_code=$?
    set -e
    end_time=$(date +%s)
    text_highlight; echo "=> Completed $1 in $((end_time - start_time)) seconds with exit code $exit_code"; text_default
    [[ $exit_code = 0 ]] || exit $exit_code
}

run ./0_0_make_phenos_json.py
run ./0_1_get_cpras_from_each_input_file.py # check dates of src_filenames (hard from Makefile, maybe easiest from python. snakemake?)
run ./0_2_get_cpras_to_show.py # check dates of cpra/*
run ./1_2_download_rsids.sh
run ./1_3_download_genes.sh
run ./1_4_make_cpra_rsids.py
run ./1_5_make_cpra_rsids_genes.sh # (make lexicographic & adds genes)
run ./1_6_make_sites_lexicographic.py # (combines dupe-lines)
run ./1_7_make_sites.sh # (fixes sort order) (assumes 1-22)
run ./1_8_make_tries.py
run ./3_1_standardize_each_pheno.py # each depends on its own src_filename, and also on sites.tsv
run ./3_2_make_manhattan_for_each_pheno.py # each depends on its own augmented_pheno
run ./3_3_make_QQ_for_each_pheno.py # each depends on its own augmented_pheno
run ./4_1_make_matrix.sh # depends on augmented_pheno/*
run ./4_2_bgzip.sh
run ./5_1_bgzip_augmented_phenos.sh # each depends on its own augmented_pheno
# delete unneeded files?
}