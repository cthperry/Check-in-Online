"""產生含台灣日期時間、可篩選的 Excel 活頁簿。"""
from datetime import datetime, timedelta
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from .store import TAIPEI


def local_time(stamp):
    if stamp is None:
        return None
    return datetime.fromtimestamp(stamp, TAIPEI).replace(tzinfo=None)


def format_sheet(sheet, widths):
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(name="微軟正黑體", color="FFFFFF", bold=True)
        cell.fill = PatternFill("solid", fgColor="175D58")
    for idx, width in enumerate(widths,1):
        sheet.column_dimensions[get_column_letter(idx)].width = width


def write_text(sheet, row, index, value):
    cell = sheet.cell(row,index,value)
    cell.data_type = "s"


def workbook(con, rows):
    book = Workbook()
    sheet = book.active
    sheet.title = "出勤明細"
    sheet.append(["班次編號","員工帳號","姓名","歸屬日期","上班時間","下班時間","在班時數","狀態"])
    changes = book.create_sheet("補登紀錄")
    changes.append(["班次編號","員工帳號","姓名","原下班時間","更正後下班時間","備註說明","經手人","登錄時間","原上班時間","更正後上班時間"])
    change_row = 1
    for n,row in enumerate(rows,2):
        start = local_time(row["started"])
        end = None if row["ended"] is None else local_time(row["ended"])
        sheet.append([row["id"],None,None,start.date(),start,end,
                      None if end is None else timedelta(seconds=row["ended"]-row["started"]),
                      "待下班" if end is None else ("已更正" if row["corrected"] else "已下班")])
        write_text(sheet,n,2,row["account"])
        write_text(sheet,n,3,row["name"])
        for col in (5,6):
            sheet.cell(n,col).number_format = "yyyy-mm-dd hh:mm:ss"
        sheet.cell(n,7).number_format = "[h]:mm:ss"
        if row["corrected"]:
            for event in con.execute("""
                SELECT id,prior_end,new_end,reason,operator,recorded,NULL AS prior_start,NULL AS new_start,0 AS source
                FROM corrections WHERE shift_id=?
                UNION ALL
                SELECT id,prior_end,new_end,reason,operator,recorded,prior_start,new_start,1 AS source
                FROM shift_edits WHERE shift_id=?
                ORDER BY recorded,source,id""", (row["id"],row["id"])):
                change_row += 1
                changes.append([row["id"],None,None,local_time(event["prior_end"]),local_time(event["new_end"]),
                                None,None,local_time(event["recorded"]),local_time(event["prior_start"]),
                                local_time(event["new_start"])])
                for col in (4,5,8,9,10):
                    changes.cell(change_row,col).number_format = "yyyy-mm-dd hh:mm:ss"
                for col, value in ((2,row["account"]),(3,row["name"]),(6,event["reason"]),(7,event["operator"])):
                    write_text(changes,change_row,col,value)
    format_sheet(sheet,[12,18,22,16,25,25,18,12])
    format_sheet(changes,[12,18,22,25,25,40,18,25,25,25])
    stream = BytesIO()
    book.save(stream)
    return stream.getvalue()
