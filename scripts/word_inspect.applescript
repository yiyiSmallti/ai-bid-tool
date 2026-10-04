-- Opens a DOCX in Microsoft Word, reports layout facts and saves a PDF rendition.
on run argv
	set docPath to item 1 of argv
	set pdfPath to item 2 of argv
	tell application "Microsoft Word"
		with timeout of 900 seconds
			close every document saving no
			open (POSIX file docPath)
			repeat 120 times
				if (count of documents) > 0 then exit repeat
				delay 1
			end repeat
			set d to active document
			set pageCount to compute statistics d statistic statistic pages
			set tableCount to count of tables of d
			set rowCounts to {}
			repeat with i from 1 to tableCount
				set end of rowCounts to (count of rows of table i of d)
			end repeat
			set AppleScript's text item delimiters to ","
			set rowText to rowCounts as text
			set pictureCount to count of inline shapes of d
			set bookmarkCount to count of bookmarks of d
			set protectionState to (protection type of d) as text
			set readOnly to read only of d
			save as d file name pdfPath file format format PDF
			close d saving no
		end timeout
	end tell
	return "pages=" & pageCount & ";tables=" & tableCount & ";rows=" & rowText & ";pictures=" & pictureCount & ";bookmarks=" & bookmarkCount & ";protection=" & protectionState & ";read_only=" & readOnly
end run
