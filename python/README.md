# Grading utilities which interact directly with the backend without browser

## `grading.py`

 `grading.py` is based on reversing the traffic in Peppi to submit grades for the students and using the user cookies grabbed from the browser, including the CSRF token. 
  - It fetches the student list as HTML based on the course identifier from Peppi, and parses the HTML table.
  - Information is mapped with the grading information from the exported Moodle `.csv` file
  - Peppi AJAX API is used to submit the grade of the every student individually, it should handle and report errors gracefully.

Currently `grading.py` does not probably work properly if there are students with completely identical name. Unfortunately, Peppi does not currently give the "OID" student identifier along the participant list in HTML table, which makes mapping the grading information difficult.

There is a search API in Peppi which could be used to verify the correct student in the future.