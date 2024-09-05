"""Python Script for Curation."""

import ast
import json
import math
import os
import random
import re
from typing import List, Tuple
import logging
import pandas as pd
from pathlib import Path
from pydantic import BaseModel, FilePath


class AnnotationData(BaseModel):
    """Pydantic model for annotation data."""

    annotation_folder: FilePath
    extract_json: FilePath
    kpi_mapping_path: FilePath


class Curator:
    """A data curator component responsible for creating table and text training data based on annotated data.

    Args:
    ----
        annotation_folder (str): path to the folder containing annotation files
        extract_json (str): path to the JSON file containing extracted content
        kpi_mapping_path (str): path to KPI Mapping csv
        neg_pos_ratio (int): ratio of negative to positive examples
        create_neg_samples (bool): whether to create negative samples

    """

    def __init__(
        self,
        annotation_folder: str,
        extract_json: Path,
        kpi_mapping_path: str,
        neg_pos_ratio: int = 1,
        create_neg_samples: bool = False,
    ) -> None:
        """Initialize the constructor for Curator object."""
        self.annotation_folder = annotation_folder
        self.extract_json = extract_json
        self.json_file_name = os.path.basename(extract_json).replace("_output", "")
        self.kpi_mapping_path = kpi_mapping_path
        self.neg_pos_ratio = neg_pos_ratio
        self.create_neg_samples = create_neg_samples

        self.pdf_content = self.load_pdf_content()

    def load_pdf_content(self) -> dict:
        """Load PDF content from the JSON file specified by `extract_json`.

        Reads the content of the JSON file and returns it as a dictionary.

        Returns:
        -------
            dict: A dictionary containing the loaded JSON data.

        Raises:
        ------
            FileNotFoundError: If the JSON file specified by `extract_json` does not exist.
            JSONDecodeError: If the content of the JSON file cannot be decoded.

        Note:
        ----
            This method assumes `extract_json` is a `Path` object pointing to a valid JSON file.

        """
        with self.extract_json.open() as f:
            return json.load(f)

    @staticmethod
    def clean_text(text: str) -> str:
        """Clean a sentence by removing unwanted characters and control characters.

        Args:
        ----
            text (str): The text to be cleaned.

        Returns:
        -------
            str: The cleaned text.

        """
        if text is None or isinstance(text, float) and math.isnan(text) or text == "":
            return ""

        text = re.sub(r"[“”]", '"', text)
        text = re.sub(r"(?<=\[)“", '"', text)
        text = re.sub(r"”(?=])", '"', text)
        text = re.sub(r"[\n\t]", " ", text)
        text = re.sub(r"[^\x20-\x7E\x0A\x0D\x09]", "", text)
        text = re.sub(r"\s{2,}", " ", text)
        text = text.replace("BOE", "")
        text = text.replace("\x9d", "")
        text = text.replace("\\", "")
        return text

    def create_pos_examples(self, row: pd.Series) -> Tuple[List[str], bool]:
        """Create positive examples based on the provided row from a DataFrame.

        Returns a list of matching sentences or an empty list, along with a flag indicating if sentences were found in the JSON.
        """
        value: str = row["relevant_paragraphs"]
        cleaned_value: str = self.clean_text(value)

        try:
            cleaned_value_list = ast.literal_eval(cleaned_value)
            if isinstance(cleaned_value_list, list):
                sentences = cleaned_value_list
            else:
                sentences = [cleaned_value]
        except (ValueError, SyntaxError):
            return ([(None, "")], False)  # Return with in_json_flag as False

        if (
            not sentences
            or self.json_file_name.replace(".json", "")
            != row["source_file"].replace(".pdf", "")
            or row["data_type"] != "TEXT"
        ):
            return ([(None, "")], False)  # Return with in_json_flag as False

        source_page = str(row["source_page"])

        match = re.search(r"\d+", source_page)
        page_number = match.group() if match else None

        if page_number in self.pdf_content:
            matching_sentences = [
                (key_inner, para)
                for key_inner in self.pdf_content[page_number]
                for para in [self.pdf_content[page_number][key_inner]["paragraph"]]
                if any(sentence in para for sentence in sentences)
            ]

            # Flag to know if sentence is available in json or not
            in_json_flag = bool(matching_sentences)
            return (
                matching_sentences if matching_sentences else [(None, sentence) for sentence in sentences],
                in_json_flag,
            )

        return ([(None, "")], False)  # Return with in_json_flag as False

    def create_neg_examples(self, row: pd.Series) -> List[str]:
        """Create negative examples based on the provided row from a DataFrame.

        Returns a list of context paragraphs or an empty list.
        """
        if (
            not self.pdf_content
            or self.json_file_name.replace(".json", "")
            != row["source_file"].replace(".pdf", "")
            or row["data_type"] != "TEXT"
        ):
            return [""]

        paragraphs = [
            self.pdf_content[key_outer][key_inner]["paragraph"]
            for key_outer in self.pdf_content
            for key_inner in self.pdf_content[key_outer]
        ]

        context = random.choices(paragraphs[1:], k=self.neg_pos_ratio)
        return context

    def create_examples_annotate(self) -> List[pd.DataFrame]:
        """Create examples for annotation.

        Returns
        -------
            List[pd.DataFrame]: List of DataFrames containing the examples to be annotated.

        """
        df = pd.read_excel(self.annotation_folder, sheet_name="data_ex_in_xls")
        df["annotation_file"] = os.path.basename(self.annotation_folder)

        # Update the "source_page" column
        df["source_page"] = df["source_page"].apply(
            lambda x: [str(p - 1) for p in ast.literal_eval(x)]
        )

        new_dfs: List[pd.DataFrame] = []

        new_dfs = []

        for i, row in df.iterrows():
            if self.json_file_name.replace(".json", "") == row["source_file"].replace(".pdf", ""):
                row["annotation_file_row"] = i

                # Create positive examples and get the in_json_flag
                pos_examples, in_json_flag = self.create_pos_examples(row.copy())

                row["in_extraction_data_flag"] = in_json_flag

                # Extract key_inner and paragraph from pos_examples
                pos_contexts = [(para, key_inner) for key_inner, para in pos_examples]

                # Prepare contexts (positive and negative examples)
                contexts = [
                    (pos_contexts, 1),
                    (
                        [(neg_example, None) for neg_example in self.create_neg_examples(row.copy())]
                        if self.create_neg_samples else [],
                        0,
                    ),
                ]

                for context, label in contexts:
                    if context:
                        # Convert context and label to DataFrame
                        context_df = pd.DataFrame(
                            {"context": [ctx[0] for ctx in context], "label": label, "unique_paragraph_id": [ctx[1] for ctx in context]}
                        )
                        combined_df = pd.concat(
                            [row.to_frame().T.reset_index(drop=True), context_df],
                            axis=1,
                        )
                        new_dfs.append(combined_df)

        return new_dfs

    '''def create_curator_df(self) -> pd.DataFrame:
        """Create a DataFrame containing the examples to be annotated by the curator.

        The DataFrame is saved as a CSV file in the output directory.
        """
        # Set up logging
        log_dir = Path('LOG')
        log_dir.mkdir(parents=True, exist_ok=True)  # Create the directory if it doesn't exist

        # Set up logging
        logging.basicConfig(
            filename=log_dir / "curation_df_log.txt",
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s"
        )

        # Define the order of columns for the final DataFrame
        columns_order = [
            "company",
            "year",
            "source_file",
            "source_page",
            "context",
            "question",
            "kpi_id",
            "label",
            "in_extraction_data_flag",
            "unique_paragraph_id",
            "annotation_file_name",
            "annotation_file_row",
            "annotation_answer",
        ]

        refused_rows = []  # To store rows that are excluded

        if self.pdf_content:  # Check if pdf_content is not empty

            new_dfs = self.create_examples_annotate()
            if new_dfs:
                new_df = pd.concat(new_dfs, ignore_index=True)

                # Load the KPI mapping and merge with the newly created DataFrame
                kpi_df = pd.read_csv(
                    self.kpi_mapping_path, usecols=["kpi_id", "question"]
                )
                merged_df = pd.merge(new_df, kpi_df, on="kpi_id", how="left")

                result_df = merged_df.rename(columns={"answer": "annotation_answer"})

                # Exclude rows where annotation_answer is not in the context
                for index, row in result_df.iterrows():
                    if str(row["annotation_answer"]) not in str(row["context"]):
                        refused_rows.append(row["annotation_file_row"])
                        logging.warning(
                            f"Row {row['annotation_file_row']} excluded: "
                            f"annotation_answer '{row['annotation_answer']}' not in context."
                        )
                        result_df.drop(index, inplace=True)

                # Handle flags and IDs
                result_df.loc[result_df["label"] == 0, "in_extraction_data_flag"] = bool(0)
                result_df.loc[
                    result_df["in_extraction_data_flag"] == 0, "unique_paragraph_id"
                ] = None
                result_df["annotation_file_name"] = Path(self.annotation_folder).name

                # Reorder columns as specified in columns_order, handling missing columns
                for col in columns_order:
                    if col not in result_df.columns:
                        result_df[col] = None  # or np.nan for numeric default
                result_df = result_df[columns_order]
                result_df = result_df.reset_index(drop=True)

                # Log summary
                logging.info(f"{len(refused_rows)} rows were refused.")
                logging.info(f"Refused rows: {refused_rows}")

        return result_df
    '''

    def create_curator_df(self) -> pd.DataFrame:
        """Create a DataFrame containing the examples to be annotated by the curator.

        The DataFrame is saved as a CSV file in the output directory.
        """
        # Define the order of columns for the final DataFrame
        columns_order = [
            "company",
            "year",
            "source_file",
            "source_page",
            "context",
            "question",
            "kpi_id",
            "label",
            "in_extraction_data_flag",
            "unique_paragraph_id",
            "annotation_file_name",
            "annotation_file_row",
            "annotation_answer",
            
        ]

        if self.pdf_content:  # Check if pdf_content is not empty
           
            new_dfs = self.create_examples_annotate()
            if new_dfs:
                
                new_df = pd.concat(new_dfs, ignore_index=True)

                # Load the KPI mapping and merge with the newly created DataFrame
                kpi_df = pd.read_csv(
                    self.kpi_mapping_path, usecols=["kpi_id", "question"]
                )
                merged_df = pd.merge(new_df, kpi_df, on="kpi_id", how="left")

                result_df = merged_df.rename(columns={"answer": "annotation_answer"})

                result_df.loc[result_df["label"] == 0, "in_extraction_data_flag"] = (
                    bool(0)
                )
                result_df.loc[result_df["in_extraction_data_flag"] == 0, "unique_paragraph_id"] = (
                    None
                )
                result_df["annotation_file_name"] = Path(self.annotation_folder).name
                
                # Reorder columns as specified in columns_order
                result_df = result_df[columns_order]
                result_df = result_df.reset_index(drop=True)

        return result_df
