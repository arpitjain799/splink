import datetime
import os

import boto3
import awswrangler as wr

from splink.misc import ensure_is_list
from splink.splink_dataframe import SplinkDataFrame


class boto_utils:
    def __init__(
        self,
        linker,
    ):
        if not type(linker.boto3_session) == boto3.session.Session:
            raise ValueError("Please enter a valid boto3 session object.")
            
        self.bucket = linker.output_bucket.replace("s3://", "")

        # If the default folder is blank, name it splink_warehouse
        # add a unique session id
        if linker.output_filepath:
            self.s3_output_name_prefix = linker.output_filepath
        else:
            self.s3_output_name_prefix = "splink_warehouse"
                
        self.session_id = linker._cache_uid
        self.s3_output = self.get_table_dir()

    def get_table_dir(self):
        out_path = os.path.join(
            "s3://",
            self.bucket,
            self.s3_output_name_prefix,
            self.session_id,
        )
        if out_path[-1] != "/":
            out_path += "/"

        return out_path


def _verify_athena_inputs(database, bucket, boto3_session):
    def generic_warning_text():
        return (
            f"\nThe supplied {database_bucket_txt} that you have requested to write to "
            f"{do_does_grammar[0]} not currently exist. \n \nCreate "
            f"{do_does_grammar[1]} either directly from within AWS, or by using "
            "'awswrangler.athena.create_athena_bucket' for buckets or "
            "'awswrangler.catalog.create_database' for databases using the "
            "awswrangler API."
        )

    errors = []

    if (
        database
        not in wr.catalog.databases(limit=None, boto3_session=boto3_session).values
    ):
        errors.append(f"database '{database}'")

    if bucket not in wr.s3.list_buckets(boto3_session=boto3_session):
        errors.append(f"bucket '{bucket}'")

    if errors:
        database_bucket_txt = " and ".join(errors)
        do_does_grammar = ["does", "it"] if len(errors) == 1 else ["do", "them"]
        raise Exception(generic_warning_text())


def _garbage_collection(
    database_name,
    boto3_session,
    delete_s3_folders=True,
    tables_to_exclude=[],
):
    tables_to_exclude = ensure_is_list(tables_to_exclude)
    tables_to_exclude = [df.physical_name if isinstance(df, SplinkDataFrame) 
                    else df 
                    for df in tables_to_exclude]
    
    # This will only delete tables created within the splink process. These are
    # tables containing the specific prefix: "__splink"
    tables = wr.catalog.get_tables(
        database=database_name,
        name_prefix="__splink",
        boto3_session=boto3_session,
    )
    delete_metadata_loc = []
    for t in tables:
        # Don't overwrite input tables if they have been
        # given the __splink prefix.
        if t["Name"] not in tables_to_exclude:
            wr.catalog.delete_table_if_exists(
                database=t["DatabaseName"],
                table=t["Name"],
                boto3_session=boto3_session,
            )
            # Only delete the backing data if requested
            if delete_s3_folders:
                path = t["StorageDescriptor"]["Location"]
                wr.s3.delete_objects(
                    path=path,
                    use_threads=True,
                    boto3_session=boto3_session,
                )
                metadata_loc = f"{path.split('/__splink')[0]}/tables/"
                if metadata_loc not in delete_metadata_loc:
                    wr.s3.delete_objects(
                        path=metadata_loc,
                        use_threads=True,
                        boto3_session=boto3_session,
                    )
                    delete_metadata_loc.append(metadata_loc)
