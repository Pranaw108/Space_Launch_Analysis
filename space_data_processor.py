import logging
import os
import urllib.parse
from pathlib import Path
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from dotenv import load_dotenv

from eda_analyzer import EDAanalyzer

# === CONFIGURATION ===
load_dotenv()

logging.basicConfig(
    filename="space_data_processor.log",
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    filemode="a"
)

DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "space_analytics")

# Configurable Write Mode (Defaults to 'replace' if not in .env)
DB_WRITE_MODE = os.getenv("DB_WRITE_MODE", "replace")

SAFE_PASSWORD = urllib.parse.quote_plus(DB_PASSWORD)
MYSQL_CONNECTION = f"mysql+mysqlconnector://{DB_USER}:{SAFE_PASSWORD}@{DB_HOST}/{DB_NAME}"

TABLES = {
    "launches": "launches",
    "rockets": "rockets",
    "enhanced": "enhanced_launches",
    "isro_summary": "isro_orbit_mass_summary",
    "launch_sites": "launch_sites"
}



# Get the absolute path of the directory containing this script
BASE_DIR = Path(__file__).resolve().parent

OUTPUT_DIR = BASE_DIR / "outputs"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# === CLASS DEFINITION ===
class SpaceDataProcessor:
    def __init__(self, db_connection: str):
        self.db_connection = db_connection
        self.engine = self._create_engine()

    def _create_engine(self):
        try:
            engine = create_engine(self.db_connection, pool_pre_ping=True)
            logging.info("Database engine created successfully.")
            return engine
        except SQLAlchemyError as e:
            logging.error(f"Database connection failed: {e}")
            return None

    def load_csv(self, file_path: Path) -> pd.DataFrame:
        try:
            df = pd.read_csv(file_path)
            logging.info(f"Loaded {file_path.name} | Shape={df.shape}")
            return df
        except FileNotFoundError:
            logging.error(f"File not found: {file_path}")
        except Exception as e:
            logging.error(f"Error loading {file_path}: {e}")
        return pd.DataFrame()

    def validate_columns(self, df: pd.DataFrame, required_columns: list, dataset_name: str) -> bool:
        missing = set(required_columns) - set(df.columns)
        if missing:
            logging.error(f"{dataset_name} missing columns: {missing}")
            return False
        return True

    def assess_data_quality(self, df: pd.DataFrame, dataset_name: str) -> pd.DataFrame:
        logging.info(f"Assessing quality for {dataset_name}")
        return pd.DataFrame({
            "Metric": ["Missing Values", "Duplicate Rows", "Invalid Launch Costs", "Invalid Payload Mass"],
            "Count": [
                df.isnull().sum().sum(),
                df.duplicated().sum(),
                (df.get("launch_cost_m_usd", pd.Series(dtype=float)) < 0).sum(),
                (df.get("payload_mass_kg", pd.Series(dtype=float)) <= 0).sum()
            ]
        })

    def engineer_business_metrics(self, launches: pd.DataFrame, rockets: pd.DataFrame) -> pd.DataFrame:
        required_launch_cols = ["rocket", "payload_mass_kg", "launch_cost_m_usd", "launch_success", "failure_mode"]
        required_rocket_cols = ["rocket", "payload_to_leo_kg"]

        if not self.validate_columns(launches, required_launch_cols, "launches"):
            return launches
        if not self.validate_columns(rockets, required_rocket_cols, "rockets"):
            return launches

        df = launches.copy()
        df = df.merge(rockets[["rocket", "payload_to_leo_kg"]], on="rocket", how="left")

        df["failure_mode"] = df["failure_mode"].fillna("None - Successful Launch")

        missing_costs_before = df["launch_cost_m_usd"].isna().sum()
        
        df["launch_cost_m_usd"] = (
            df.groupby("rocket")["launch_cost_m_usd"]
              .transform(lambda x: x.fillna(x.median()))
        )
        
        if "operator" in df.columns:
            df["launch_cost_m_usd"] = df.groupby("operator")["launch_cost_m_usd"].transform(lambda x: x.fillna(x.median()))
            
        # Fill :- using the median of the ROCKET ERA (Cold War, Modern, etc.)
        if "rocket_era" in df.columns:
            df["launch_cost_m_usd"] = df.groupby("rocket_era")["launch_cost_m_usd"].transform(lambda x: x.fillna(x.median()))


        missing_costs_after = df["launch_cost_m_usd"].isna().sum()
        imputed_count = missing_costs_before - missing_costs_after
        if imputed_count > 0:
            logging.warning(f"Imputed {imputed_count} missing launch costs using rocket medians. {missing_costs_after} still unresolvable.")

        df["cost_per_kg_usd"] = np.where(
            df["payload_mass_kg"] > 0,
            (df["launch_cost_m_usd"] * 1_000_000) / df["payload_mass_kg"],
            np.nan
        )
        
        df["payload_utilization_pct"] = np.where(
            df["payload_to_leo_kg"] > 0,
            (df["payload_mass_kg"] / df["payload_to_leo_kg"]) * 100,
            np.nan
        )

        df["mission_outcome"] = np.select(
            [
                df["launch_success"] == 1,
                df["failure_mode"].str.contains("partial", case=False, na=False)
            ],
            [
                "Success",
                "Partial Failure"
            ],
            default="Failure"
        )
        return df

    def analyze_launch_trends(self, df: pd.DataFrame) -> Tuple[Optional[pd.Series], Optional[pd.Series], Optional[pd.Series]]:
        try:
            df = df.copy()
            df = df.dropna(subset=["date"]).sort_values("date").set_index("date")

            monthly = df.resample("ME").size().rename("monthly_launches")
            yearly = df.resample("YE").size().rename("yearly_launches")
            rolling_success = df.resample("ME")["launch_success"].mean().rolling(window=12, min_periods=1).mean() * 100

            return monthly, yearly, rolling_success
        except Exception as e:
            logging.error(f"Trend analysis failed: {e}")
            return None, None, None

    def isro_analysis(self, df: pd.DataFrame) -> pd.DataFrame:
        try:
            df.columns = [col.strip().replace(" ", "_").replace("(", "").replace(")", "").replace(".", "")
                         for col in df.columns]
            if not {"Launch_Mass_kg", "Class_of_Orbit"}.issubset(df.columns):
                logging.warning("Required ISRO columns missing.")
                return pd.DataFrame()
            return df.groupby("Class_of_Orbit", as_index=False)["Launch_Mass_kg"].sum()
        except Exception as e:
            logging.error(f"ISRO analysis failed: {e}")
            return pd.DataFrame()

    def save_to_db(self, df: pd.DataFrame, table_name: str, if_exists: str = "replace"):
        if self.engine is None:
            logging.error("Database engine unavailable.")
            return
        try:
            with self.engine.begin() as conn:
                df.to_sql(table_name, conn, if_exists=if_exists, index=False)
            logging.info(f"Saved {len(df)} rows to table {table_name} (Mode: {if_exists})")
        except Exception as e:
            logging.error(f"Database write failed for {table_name}: {e}")


# === MAIN PIPELINE ===
def main():
    processor = SpaceDataProcessor(MYSQL_CONNECTION)
    if processor.engine is None:
        logging.error("Database connection unavailable. Exiting pipeline.")
        return
    
    dataset_dir = Path(__file__).parent / "datasets"

    launches = processor.load_csv(dataset_dir / "launches.csv")
    rockets = processor.load_csv(dataset_dir / "rockets.csv")
    launch_sites = processor.load_csv(dataset_dir / "launch_sites.csv")
    isro = processor.load_csv(dataset_dir / "ISRO Satellite Dataset.csv")

    if launches.empty or rockets.empty:
        logging.error("Required datasets missing. Exiting pipeline.")
        return

    launches["date"] = pd.to_datetime(launches["date"], errors="coerce")

    # Data quality
    quality_report = processor.assess_data_quality(launches, "launches")
    quality_report.to_csv(OUTPUT_DIR / "data_quality_report.csv", index=False)
    print("\n=== DATA QUALITY REPORT ===")
    print(quality_report)

    # Business metrics 
    enhanced = processor.engineer_business_metrics(launches, rockets)

    # Save to DB using the configured write mode (replace or append)
    processor.save_to_db(launches, TABLES["launches"], if_exists=DB_WRITE_MODE)
    processor.save_to_db(rockets, TABLES["rockets"], if_exists=DB_WRITE_MODE)
    processor.save_to_db(enhanced, TABLES["enhanced"], if_exists=DB_WRITE_MODE)
    
    if not launch_sites.empty:
        required_site_cols = ["site_name", "country", "latitude", "longitude"]
        if processor.validate_columns(launch_sites, required_site_cols, "launch_sites"):
            processor.save_to_db(launch_sites, TABLES["launch_sites"], if_exists=DB_WRITE_MODE)

    if not isro.empty:
        isro_summary = processor.isro_analysis(isro)
        if not isro_summary.empty:
            processor.save_to_db(isro_summary, TABLES["isro_summary"], if_exists=DB_WRITE_MODE)

    # EDA
    try:
        eda = EDAanalyzer(MYSQL_CONNECTION, output_dir=str(REPORTS_DIR))
        eda.run()
    except Exception as e:
        logging.error(f"EDA execution failed: {e}")

    # Trend analysis
    monthly, yearly, rolling = processor.analyze_launch_trends(enhanced)
    if rolling is not None:
        monthly.to_csv(OUTPUT_DIR / "monthly_launches.csv")
        yearly.to_csv(OUTPUT_DIR / "yearly_launches.csv")
        rolling.to_csv(OUTPUT_DIR / "rolling_success_rate.csv")

    enhanced.to_csv(OUTPUT_DIR / "enhanced_launches.csv", index=False)
    
    logging.info("Pipeline completed successfully.")
    print("\nPipeline completed successfully.")


if __name__ == "__main__":
    main()