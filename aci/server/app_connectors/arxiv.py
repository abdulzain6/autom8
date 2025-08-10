import datetime
import importlib
from typing import List, Dict, Any
from aci.common.db.sql_models import LinkedAccount
from aci.common.logging_setup import get_logger
from aci.common.schemas.security_scheme import NoAuthScheme, NoAuthSchemeCredentials
from aci.server.app_connectors.base import AppConnectorBase


logger = get_logger(__name__)
arxiv = importlib.import_module('arxiv')


class Arxiv(AppConnectorBase):
    """
    Connector for searching and retrieving academic papers from the arXiv API.
    Provides a structured, AI-friendly interface to arXiv's vast repository.
    """

    def __init__(
        self,
        linked_account: LinkedAccount,
        security_scheme: NoAuthScheme,
        security_credentials: NoAuthSchemeCredentials,
    ):
        """
        Initializes the ArxivConnector.
        """
        super().__init__(linked_account, security_scheme, security_credentials)
        self.client = arxiv.Client(page_size=20, delay_seconds=3, num_retries=3)
        logger.info("ArxivConnector initialized.")

    def _before_execute(self) -> None:
        pass

    def _format_result(self, result: arxiv.Result) -> Dict[str, Any]: # type: ignore
        """
        Helper function to convert an arxiv.Result object into a clean dictionary.
        """
        return {
            "entry_id": result.entry_id,
            "title": result.title,
            "authors": [author.name for author in result.authors],
            "summary": result.summary,
            "comment": result.comment,
            "journal_ref": result.journal_ref,
            "doi": result.doi,
            "primary_category": result.primary_category,
            "categories": result.categories,
            "published_date": result.published.isoformat(),
            "updated_date": result.updated.isoformat(),
            "pdf_url": result.pdf_url,
        }

    def search_papers(
        self, query: str, max_results: int = 10, sort_by: str = "relevance"
    ) -> List[Dict[str, Any]]:
        """
        Searches for papers on arXiv by query.

        Args:
            query: The search query (e.g., 'quantum computing' or 'au:Del_Maestro AND ti:checkerboard').
            max_results: The maximum number of results to return.
            sort_by: The criterion for sorting results. Can be 'relevance', 'lastUpdatedDate', or 'submittedDate'.

        Returns:
            A list of dictionaries, each representing a paper.
        """
        logger.info(f"Searching arXiv for query='{query}', max_results={max_results}")
        sort_criterion_map = {
            "relevance": arxiv.SortCriterion.Relevance,
            "lastUpdatedDate": arxiv.SortCriterion.LastUpdatedDate,
            "submittedDate": arxiv.SortCriterion.SubmittedDate,
        }
        sort_criterion = sort_criterion_map.get(sort_by, arxiv.SortCriterion.Relevance)

        search = arxiv.Search(
            query=query, max_results=max_results, sort_by=sort_criterion
        )

        results_generator = self.client.results(search)
        return [self._format_result(result) for result in results_generator]

    def get_paper_metadata(self, paper_id: str) -> Dict[str, Any]:
        """
        Retrieves detailed metadata for a specific paper on arXiv by its ID.

        Args:
            paper_id: The arXiv ID of the paper (e.g., '1605.08386v1' or 'math.GT/0309136').

        Returns:
            A dictionary containing the paper's metadata.
        """
        logger.info(f"Fetching metadata for arXiv paper ID: {paper_id}")
        try:
            search = arxiv.Search(id_list=[paper_id])
            result = next(self.client.results(search))
            return self._format_result(result)
        except StopIteration:
            raise ValueError(f"Paper with ID '{paper_id}' not found.") from None

    def get_daily_updates(
        self, category: str, date: str | None = None, max_results: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Fetches papers submitted to a specific category on a given date.

        Args:
            category: The category to check (e.g., 'cs.AI', 'econ.EM').
            date: The date in 'YYYY-MM-DD' format. Defaults to today.
            max_results: The maximum number of papers to return.

        Returns:
            A list of papers submitted on that day in that category.
        """
        if date:
            target_date = datetime.datetime.strptime(date, "%Y-%m-%d").date()
        else:
            target_date = datetime.date.today()

        logger.info(
            f"Fetching daily updates for '{category}' on {target_date.isoformat()}"
        )

        # arXiv API query syntax for a specific submission date.
        query = f"cat:{category} AND submittedDate:[{target_date.strftime('%Y%m%d')}0000 TO {target_date.strftime('%Y%m%d')}2359]"
        return self.search_papers(
            query=query, max_results=max_results, sort_by="submittedDate"
        )

    @staticmethod
    def get_categories() -> Dict[str, Any]:
        """
        Retrieves a structured list of all available categories on arXiv.
        The arXiv API does not provide this, so it's maintained as a static list.
        """
        return {
            "Computer Science": [
                "cs.AI",
                "cs.AR",
                "cs.CC",
                "cs.CE",
                "cs.CG",
                "cs.CL",
                "cs.CR",
                "cs.CV",
                "cs.CY",
                "cs.DB",
                "cs.DC",
                "cs.DL",
                "cs.DM",
                "cs.DS",
                "cs.ET",
                "cs.FL",
                "cs.GL",
                "cs.GR",
                "cs.GT",
                "cs.HC",
                "cs.IR",
                "cs.IT",
                "cs.LG",
                "cs.LO",
                "cs.MA",
                "cs.MM",
                "cs.MS",
                "cs.NA",
                "cs.NE",
                "cs.NI",
                "cs.OH",
                "cs.OS",
                "cs.PF",
                "cs.PL",
                "cs.RO",
                "cs.SC",
                "cs.SD",
                "cs.SE",
                "cs.SI",
                "cs.SY",
            ],
            "Economics": ["econ.EM", "econ.GN", "econ.TH"],
            "Electrical Engineering and Systems Science": [
                "eess.AS",
                "eess.IV",
                "eess.SP",
                "eess.SY",
            ],
            "Mathematics": [
                "math.AC",
                "math.AG",
                "math.AP",
                "math.AT",
                "math.CA",
                "math.CO",
                "math.CT",
                "math.CV",
                "math.DG",
                "math.DS",
                "math.FA",
                "math.GM",
                "math.GN",
                "math.GR",
                "math.GT",
                "math.HO",
                "math.IT",
                "math.KT",
                "math.LO",
                "math.MG",
                "math.MP",
                "math.NA",
                "math.NT",
                "math.OA",
                "math.OC",
                "math.PR",
                "math.QA",
                "math.RA",
                "math.RT",
                "math.SG",
                "math.SP",
                "math.ST",
            ],
            "Physics": [
                "astro-ph.CO",
                "astro-ph.EP",
                "astro-ph.GA",
                "astro-ph.HE",
                "astro-ph.IM",
                "astro-ph.SR",
                "cond-mat.dis-nn",
                "cond-mat.mes-hall",
                "cond-mat.mtrl-sci",
                "cond-mat.other",
                "cond-mat.quant-gas",
                "cond-mat.soft",
                "cond-mat.stat-mech",
                "cond-mat.str-el",
                "cond-mat.supr-con",
                "gr-qc",
                "hep-ex",
                "hep-lat",
                "hep-ph",
                "hep-th",
                "math-ph",
                "nlin.AO",
                "nlin.CD",
                "nlin.CG",
                "nlin.PS",
                "nlin.SI",
                "nucl-ex",
                "nucl-th",
                "physics.acc-ph",
                "physics.ao-ph",
                "physics.app-ph",
                "physics.atm-clus",
                "physics.atom-ph",
                "physics.bio-ph",
                "physics.chem-ph",
                "physics.class-ph",
                "physics.comp-ph",
                "physics.data-an",
                "physics.ed-ph",
                "physics.flu-dyn",
                "physics.gen-ph",
                "physics.geo-ph",
                "physics.hist-ph",
                "physics.ins-det",
                "physics.med-ph",
                "physics.optics",
                "physics.plasm-ph",
                "physics.pop-ph",
                "physics.soc-ph",
                "physics.space-ph",
                "quant-ph",
            ],
            "Quantitative Biology": [
                "q-bio.BM",
                "q-bio.CB",
                "q-bio.GN",
                "q-bio.MN",
                "q-bio.NC",
                "q-bio.OT",
                "q-bio.PE",
                "q-bio.QM",
                "q-bio.SC",
                "q-bio.TO",
            ],
            "Quantitative Finance": [
                "q-fin.CP",
                "q-fin.EC",
                "q-fin.GN",
                "q-fin.MF",
                "q-fin.PM",
                "q-fin.PR",
                "q-fin.RM",
                "q-fin.ST",
                "q-fin.TR",
            ],
            "Statistics": [
                "stat.AP",
                "stat.CO",
                "stat.ME",
                "stat.ML",
                "stat.OT",
                "stat.TH",
            ],
        }


# --- Main function for testing ---
if __name__ == "__main__":
    print("--- ArxivConnector Test ---")

    mock_scheme = NoAuthScheme()
    mock_creds = NoAuthSchemeCredentials()

    try:
        connector = Arxiv(
            linked_account=None,  # type: ignore
            security_scheme=mock_scheme,
            security_credentials=mock_creds,
        )
        print("✅ Connector initialized successfully.")

        print("\n--- Testing get_categories() ---")
        categories = connector.get_categories()
        print(f"✅ Found {len(categories)} main subject areas.")
        print(
            f"  Example: First 3 CS categories are {categories['Computer Science'][:3]}"
        )

        print("\n--- Testing search_papers(query='large language model') ---")
        papers = connector.search_papers(query="large language model", max_results=2)
        print(f"✅ Found {len(papers)} papers.")
        if papers:
            print(f"  First paper title: '{papers[0]['title']}'")
            print(f"  Entry ID: {papers[0]['entry_id']}")

        paper_to_test = "2305.10601v1"  # A known paper ID
        print(f"\n--- Testing get_paper_metadata(paper_id='{paper_to_test}') ---")
        metadata = connector.get_paper_metadata(paper_id=paper_to_test)
        print(f"✅ Metadata fetched for '{metadata['title']}'")

        print("\n--- Testing get_daily_updates(category='cs.AI') ---")
        # Use a recent, fixed date for consistent testing
        test_date = "2025-08-07"
        daily_papers = connector.get_daily_updates(
            category="cs.AI", date=test_date, max_results=3
        )
        print(f"✅ Found {len(daily_papers)} updates for 'cs.AI' from {test_date}.")
        if daily_papers:
            print(f"  First update title: '{daily_papers[0]['title']}'")

    except Exception as e:
        print(f"\n❌ An error occurred during testing: {e}")
