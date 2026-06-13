import logging
import pandas as pd
from sqlalchemy import create_engine, text
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s"
)

class EDAanalyzer:
    # 1. Added output_dir as a parameter with a default fallback
    def __init__(self, connection_string, table="enhanced_launches", output_dir="reports"):
        self.table = table
        
        # 2. Store the output directory and create it if it doesn't exist
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        try:
            self.engine = create_engine(connection_string, pool_pre_ping=True)
            logging.info("Database connected")
        except Exception as e:
            logging.error(f"Connection failed: {e}")
            self.engine = None

    # Execute SQL and return DataFrame.
    def query(self, sql):
        if not self.engine:
            return pd.DataFrame()

        try:
            return pd.read_sql(text(sql), self.engine)
        except Exception as e:
            logging.error(f"Query failed: {e}")
            return pd.DataFrame()

    def load_full_dataset(self):
        return self.query(f"""
            SELECT *
            FROM {self.table}
        """)

    def pandas_eda(self):
        df = self.load_full_dataset()

        if df.empty:
            return

        print("\n" + "-" * 30)
        print("DATASET SHAPE :- ", end="")
        print(df.shape)

        print("\n" + "-" * 30)
        print("DATASET INFO :- ", end="")
        df.info()

        print("\n" + "-" * 30)
        print("DESCRIPTIVE STATISTICS :- ", end="")
        print(df.describe(include="all"))

        print("\n" + "-" * 30)
        print("MISSING VALUES :- ", end="")
        print(df.isnull().sum())

        print("\n" + "-" * 30)
        print("CORRELATION MATRIX :- ", end="")
        numeric_cols = df.select_dtypes(include=["number"])
        print(numeric_cols.corr(numeric_only=True))

    # Dataset Overview
    def overview(self):
        sql = f"""SELECT
                COUNT(*) AS total_launches,
                COUNT(DISTINCT rocket) AS unique_rockets
            FROM {self.table}"""
        return self.query(sql)

    # Success Rate
    def success_rate(self):
        return self.query(f"""
            SELECT launch_success, COUNT(*) AS launches,
                ROUND(COUNT(*) * 100.0 / SUM(COUNT(*)) OVER (), 2) AS percentage
            FROM {self.table} GROUP BY launch_success
        """)

    # Cost Efficiency Ranking
    def cost_efficiency(self):
        return self.query(f"""
            SELECT rocket,
                ROUND(AVG(cost_per_kg_usd), 2) AS avg_cost_per_kg
            FROM {self.table} GROUP BY rocket
            ORDER BY avg_cost_per_kg
        """)
    
    # Top 10 Most Used Rockets
    def top_rockets(self):
        return self.query(f"""
            SELECT rocket, COUNT(*) AS launches
            FROM {self.table} GROUP BY rocket
            ORDER BY launches DESC LIMIT 10
        """)

    # Rocket Performance
    def rocket_performance(self):
        return self.query(f"""
            SELECT rocket, COUNT(*) AS launches,
                ROUND(AVG(launch_success) * 100, 2) AS success_rate,
                ROUND(AVG(payload_mass_kg), 2) AS avg_payload,
                ROUND(AVG(launch_cost_m_usd), 2) AS avg_cost
            FROM {self.table} GROUP BY rocket
            ORDER BY success_rate DESC
        """)

    # Business KPIs
    def kpis(self):
        return self.query(f"""
            SELECT
                COUNT(*) AS total_launches,
                ROUND(AVG(launch_cost_m_usd), 2) AS avg_cost_musd,
                ROUND(AVG(payload_mass_kg), 2) AS avg_payload_kg,
                ROUND(AVG(launch_success) * 100, 2) AS success_rate_pct
            FROM {self.table}
        """)
    
    # Rocket Reliability Ranking
    def rocket_reliability(self):
        return self.query(f"""
            SELECT rocket,
                COUNT(*) AS launches,
                ROUND(AVG(launch_success) * 100, 2) AS success_rate
            FROM {self.table} GROUP BY rocket HAVING launches >= 5
            ORDER BY success_rate DESC
        """)

    # Payload vs Success Analysis
    def payload_success_analysis(self):
        return self.query(f"""SELECT
                CASE
                    WHEN payload_mass_kg < 1000
                        THEN 'Small'
                    WHEN payload_mass_kg < 5000
                        THEN 'Medium'
                    ELSE 'Heavy'
                END AS payload_category,
                COUNT(*) AS launches,
                ROUND(AVG(launch_success) * 100, 2) AS success_rate
            FROM {self.table} GROUP BY payload_category
        """)

    # Cost vs Success Analysis
    def cost_success_analysis(self):
        return self.query(f"""SELECT
                CASE
                    WHEN launch_cost_m_usd < 50
                        THEN 'Low Cost'
                    WHEN launch_cost_m_usd < 100
                        THEN 'Medium Cost'
                    ELSE 'High Cost'
                END AS cost_bucket,
                COUNT(*) AS launches,
                ROUND(AVG(launch_success) * 100, 2) AS success_rate
            FROM {self.table} GROUP BY cost_bucket
        """)

    # Outlier Detection
    def cost_outliers(self):
        df = self.query(f"""SELECT rocket, launch_cost_m_usd FROM {self.table}""")

        if df.empty:
            return pd.DataFrame()
        
        q1 = df["launch_cost_m_usd"].quantile(0.25)
        q3 = df["launch_cost_m_usd"].quantile(0.75)

        iqr = q3 - q1

        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr

        return df[(df["launch_cost_m_usd"] < lower) | (df["launch_cost_m_usd"] > upper)]


    def run(self): 
        reports = { 
            "Dataset Overview": self.overview(), 
            "Success Rate": self.success_rate(), 
            "Cost Analysis": self.cost_efficiency(), 
            "Top Rockets": self.top_rockets(), 
            "Rocket Performance": self.rocket_performance(), 
            "Business KPIs": self.kpis(), 
            "Rocket Reliability": self.rocket_reliability(), 
            "Payload Success Analysis": self.payload_success_analysis(), 
            "Cost success Analysis": self.cost_success_analysis(),
            "Cost Outlier": self.cost_outliers() 
        }

        # 3. Use self.output_dir instead of hardcoding "reports"
        for report_name, result in reports.items():
            file_name = report_name.lower().replace(" ", "_")
            result.to_csv(self.output_dir / f"{file_name}.csv", index=False)
            print("\n" + "=" * 60)
            print(report_name.upper())
            print("=" * 60)
            print(result)

        self.pandas_eda()

        logging.info("EDA completed successfully")