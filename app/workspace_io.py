"""Bounded CSV/XLSX handling. Workbooks are data, never executable programs."""
import base64
import binascii
import csv
import json
from datetime import date,datetime
from io import BytesIO,StringIO
from pathlib import PurePosixPath
from zipfile import ZipFile,BadZipFile
from defusedxml import ElementTree
from fastapi import Response,HTTPException
from openpyxl import load_workbook,Workbook
from pydantic import BaseModel,Field
from typing import Literal


class TableUpload(BaseModel):
    format: Literal["csv","xlsx"]="csv"
    data_base64: str=Field(min_length=1,max_length=26_666_668)


def read_table(payload:TableUpload,*,max_bytes:int=5_000_000,max_rows:int=1000,max_columns:int=50)->list[dict[str,str]]:
    try:
        data=base64.b64decode(payload.data_base64,validate=True)
    except (binascii.Error,ValueError) as exc:
        raise ValueError("Provide base64-encoded CSV or XLSX bytes") from exc
    if len(data)>max_bytes:
        raise ValueError(f"File exceeds the {max_bytes:,}-byte upload limit")
    if payload.format=="csv":
        try:
            text=data.decode("utf-8-sig")
            rows=[]
            for index,row in enumerate(csv.reader(StringIO(text),strict=True)):
                if index>max_rows:
                    raise ValueError(f"Upload at most {max_rows} data rows per request")
                rows.append(row)
        except (UnicodeDecodeError,csv.Error) as exc:
            raise ValueError("Use a valid UTF-8 CSV file") from exc
        if len(rows)>max_rows+1:
            raise ValueError(f"Upload at most {max_rows} data rows per request")
    else:
        try:
            with ZipFile(BytesIO(data)) as archive:
                members=archive.infolist()
                if len(members)>1000 or sum(item.file_size for item in members)>20_000_000:
                    raise ValueError("The decompressed workbook is too large")
                for member in members:
                    path=PurePosixPath(member.filename)
                    if path.is_absolute() or ".." in path.parts or member.file_size>20_000_000:
                        raise ValueError("Unsafe workbook archive")
                    if "vbaproject" in member.filename.lower() or "externallinks" in member.filename.lower():
                        raise ValueError("Macros and external workbook links are not supported")
                    if member.filename.endswith(".rels"):
                        for relationship in ElementTree.fromstring(archive.read(member)):
                            if relationship.attrib.get("TargetMode","").lower()=="external":
                                raise ValueError("External workbook relationships are not supported")
            workbook=load_workbook(BytesIO(data),read_only=True,data_only=False,keep_links=False)
            try:
                if len(workbook.sheetnames)>10:
                    raise ValueError("Use a workbook with at most 10 worksheets")
                sheet=workbook.active
                if sheet.max_row>max_rows+1 or sheet.max_column>max_columns:
                    raise ValueError(f"Use at most {max_rows} data rows and {max_columns} columns")
                rows=[]
                for row in sheet.iter_rows(max_row=min(sheet.max_row,max_rows+1),max_col=min(sheet.max_column,max_columns)):
                    values=[]
                    for cell in row:
                        if cell.data_type in {"f","e"}:
                            raise ValueError("Replace spreadsheet formulas and error cells with plain values before importing")
                        value=cell.value
                        if isinstance(value,(date,datetime)):
                            value=value.isoformat()
                        values.append("" if value is None else str(value))
                    rows.append(values)
            finally:
                workbook.close()
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("The XLSX workbook could not be read safely") from exc
    if not rows:
        raise ValueError("A header row is required")
    fields=[str(value).strip() for value in rows[0]]
    if not fields or len(fields)>max_columns or any(not name or len(name)>80 for name in fields) or len(set(fields))!=len(fields):
        raise ValueError("Use unique, non-empty column names and at most 50 columns")
    result=[]
    for row in rows[1:]:
        if not any(str(value).strip() for value in row):
            continue
        if len(row)!=len(fields) or any(len(str(value))>10000 for value in row):
            raise ValueError("Every row must match the header and contain cells of at most 10,000 characters")
        if any("\x00" in str(value) for value in row):
            raise ValueError("Remove null bytes from the uploaded file")
        result.append(dict(zip(fields,row)))
    return result


def table_response(rows:list[dict],fields:list[str],format:str,filename:str)->Response:
    if len(rows)>10000:
        raise ValueError("Export at most 10,000 rows at once")
    if format=="json":
        content=json.dumps(rows,ensure_ascii=False,default=lambda value:value.isoformat())
        media="application/json"
    elif format=="xlsx":
        book=Workbook(write_only=True);sheet=book.create_sheet("Data")
        sheet.append(fields)
        from openpyxl.cell import WriteOnlyCell
        for row in rows:
            cells=[]
            for field in fields:
                value=row.get(field)
                if isinstance(value,(dict,list)):
                    value=json.dumps(value,ensure_ascii=False)
                if isinstance(value,(date,datetime)):
                    value=value.isoformat()
                if isinstance(value,str):
                    if any(ord(char)<32 and char not in "\t\r\n" for char in value):
                        raise HTTPException(422,detail="A field contains control characters unsupported by XLSX. Use JSON or correct the record before exporting.")
                cell=WriteOnlyCell(sheet,value=value)
                if isinstance(value,str):
                    cell.data_type="s"
                    cell.number_format="@"
                cells.append(cell)
            sheet.append(cells)
        output=BytesIO();book.save(output);content=output.getvalue()
        media="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif format=="csv":
        output=StringIO();writer=csv.DictWriter(output,fieldnames=fields);writer.writeheader()
        for row in rows:
            values={key:row.get(key,"") for key in fields}
            for key,value in values.items():
                if isinstance(value,(dict,list)):
                    value=json.dumps(value,ensure_ascii=False)
                if isinstance(value,str) and value.lstrip("".join(chr(index) for index in range(33))+"\ufeff").startswith(("=","+","-","@")):
                    value="'"+value
                values[key]=value
            writer.writerow(values)
        content=output.getvalue().encode("utf-8-sig");media="text/csv"
    else:
        raise ValueError("Choose CSV, XLSX or JSON")
    return Response(content,media_type=media,headers={"Content-Disposition":f'attachment; filename="{filename}.{format}"',"Cache-Control":"no-store"})
